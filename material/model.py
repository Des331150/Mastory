"""The one place Mastory talks to a language model.

Every model call in the application goes through this module, and all of them
obey one grounding contract:

    A call is given the student's own material and nothing else, and what comes
    back has to point at the material it came from.

The contract is enforced here rather than asked for in a prompt. A reply that
cites a slide the caller never supplied loses that citation, and a reply with
nothing left to point at is dropped rather than shown. A reply that is unsure
about itself comes back flagged, so the student is asked to check it instead of
being handed a confident guess. Quiz questions came the same way rather than
through a second seam, which is why ``generate_questions`` asks one paragraph at
a time and then verifies every answer against that paragraph before returning
it: a citation is only worth showing if what it points at supports the question.

Nothing above this module knows how a model is reached, and nothing in it knows
what a topic is. What it does know is what a call cost: every call the
application makes is counted here, and this is the only place in the application
that knows a call happened at all.
"""

import json
import logging
import re
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

#: Below this confidence the student is told the topic needs checking against
#: their own slides. Above it, the topic is shown as inferred and unremarkable.
CONFIDENCE_FLOOR = 0.6

MULTIPLE_CHOICE = "multiple_choice"
SHORT_ANSWER = "short_answer"

#: What the student is told about a topic the model was unsure of.
UNSURE = "The model was not sure of this topic. Check it against your slides."

#: How much of one slide the model is shown. Long slides are trimmed rather than
#: dropped, so a topic can still be inferred from the part that was sent.
_SLIDE_TEXT_LIMIT = 1200

#: A reply that is a Markdown code fence and nothing else, with its opening tag
#: and its language left optional. See ``_decoded``.
_FENCE = re.compile(r"\A\s*```[^\n`]*\n(?P<body>.*?)\n?```\s*\Z", re.DOTALL)

#: How many times one call is tried when the provider is throttling, and how long
#: the whole of that waiting is allowed to take.
#:
#: The budget is on the total rather than on each wait, because the limit being
#: ridden out is a window: providers with a free tier allow a fixed number of
#: requests a minute and send no ``Retry-After`` for it, so waiting has to reach
#: as far as the window that will have passed. Capping each sleep alone let a
#: provider asking for a minute buy four minutes of a student watching a spinner.
THROTTLED = 429
THROTTLE_ATTEMPTS = 4
MAX_BACKOFF_SECONDS = 45.0

#: The most time one call may spend *waiting to be retried*, whatever the
#: provider asks for. The attempts themselves are bounded by the configured
#: network timeout; this bounds what this product adds on top, so a student is
#: never held for a sum of provider wishes. It is the "wait a minute" on the
#: page, spent as waits rather than as a spinner.
RETRY_BUDGET_SECONDS = 45.0

_INSTRUCTION = (
    "You are reading one student's own course material to work out the topic "
    "path of their course. Use only the slides below; do not use outside "
    "knowledge and do not invent slides.\n"
    "The outline is a hint taken from the deck's own headings, and only if "
    "one is given. Where outline.present is false the deck has no structure to "
    "take, so work the topics out of what the slides say rather than expecting "
    "a structure that is not there.\n"
    "Return one topic per idea the course teaches, in the order the course "
    "teaches them. A topic may cover slides that are not next to each other, "
    "and its 'slides' must list the index of every slide that covers it.\n"
    "Set confidence low when the slides do not clearly support the topic or its "
    "extent; the student is shown that low confidence and asked to check it.\n"
    'Reply with JSON only: {"topics": [{"title": "...", "slides": [1, 2], '
    '"confidence": 0.0}]}'
)

#: How much of one span the model is shown. A span is a paragraph, so this only
#: bites on a wall of text, and trimming rather than dropping keeps a long
#: paragraph able to produce a question instead of silently producing none.
_SPAN_TEXT_LIMIT = 1200

_QUESTION_INSTRUCTION = (
    "You are writing quiz questions from one paragraph of one student's own "
    "course material. Use only the paragraph the question names; do not use "
    "outside knowledge, do not use another paragraph, and do not invent "
    "detail.\n"
    "Write at most one question per paragraph, and only where the paragraph "
    "actually states an answer that is not a heading. A paragraph you cannot "
    "ask about is left out of your reply rather than filled in.\n"
    'Two kinds only. "multiple_choice" needs "choices" as three or four '
    'options with exactly one of them right, and "answer" naming that option '
    'verbatim. "short_answer" needs "answers" as the word or phrase the '
    'paragraph itself uses, and nothing else.\n'
    'Reply with JSON only: {"questions": [{"span": 1, "kind": '
    '"multiple_choice", "prompt": "...", "choices": ["...", "..."], '
    '"answer": "..."}]}'
)

_VERIFY_INSTRUCTION = (
    "You are checking one answer against the paragraph it came from. Read only "
    "the paragraph and the question below. Say whether the paragraph itself "
    "states the answer to the question - not whether it is true, not whether "
    "you agree with it, and not whether you can guess it. If the paragraph does "
    "not state it, say no.\n"
    'Reply with JSON only: {"supported": true}'
)


class ModelUnavailable(Exception):
    """A model call that could not be made, in words a student can act on.

    ``reason`` says what happened and ``advice`` says what to do about it, in
    the same shape as a conversion failure.
    """

    def __init__(self, reason: str, advice: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.advice = advice


@dataclass(frozen=True)
class SlideExcerpt:
    """One slide as the model is allowed to see it.

    ``index`` is the slide's place in course order across every file of the
    course, so it is both what the model cites and what the application orders
    topics by. ``heading`` is the deck's own heading for this slide where it has
    one, which is what makes the outline a hint rather than a skeleton: a deck
    with no headings has none of these and the model is told so.
    """

    index: int
    title: str
    text: str
    heading: str = ""


@dataclass(frozen=True)
class TopicProposal:
    """One topic the model claims, after the contract has been applied.

    ``slides`` holds indices that were supplied and exist, in course order, and
    is never empty. ``flag`` is empty when there is nothing for the student to
    check and says what to check otherwise.
    """

    title: str
    slides: tuple[int, ...]
    confidence: float
    flag: str

    @property
    def flagged(self) -> bool:
        return bool(self.flag)


def infer_topics(
    *, course_title: str, slides: Sequence[SlideExcerpt]
) -> list[TopicProposal]:
    """The topics this material covers, as far as the model can tell.

    Returned in course order, each pointing only at supplied slides.
    """
    reply = complete(_topic_prompt(course_title=course_title, slides=slides))
    return _grounded_topics(reply, slides=slides)


def _topic_prompt(*, course_title: str, slides: Sequence[SlideExcerpt]) -> str:
    """The whole request as JSON: the material, and what to do with it.

    Sent as one document rather than as prose so that what the model was shown
    is readable in a log line, and so a fake at ``complete`` can read the
    material the same way a model does.

    The outline is included as a hint and is explicitly marked as possibly
    absent. Deck outlines frequently cover nothing at all, so a model told to
    expect one invents a structure the slides do not have.
    """
    return json.dumps(
        {
            "instruction": _INSTRUCTION,
            "course": course_title,
            "outline": _outline(slides),
            "slides": [
                {
                    "index": slide.index,
                    "title": slide.title,
                    "text": slide.text[:_SLIDE_TEXT_LIMIT],
                }
                for slide in slides
            ],
        }
    )


def _outline(slides: Sequence[SlideExcerpt]) -> dict[str, Any]:
    """The deck's own headings, or a plain statement that it has none.

    Reported with the slide each heading came from, so the model can use them as
    topic boundaries where they are meaningful and ignore them where they are
    not - a slide title is often the only text on a slide, which makes a
    headingless deck's titles a poor outline and not a reliable one.
    """
    headings = [
        {"index": slide.index, "heading": slide.heading}
        for slide in slides
        if slide.heading
    ]
    if not headings:
        return {"present": False, "headings": []}
    return {"present": True, "headings": headings}


def _grounded_topics(reply: str, *, slides: Sequence[SlideExcerpt]) -> list[TopicProposal]:
    """The topics a reply claims, kept only where they point at real material.

    This is the grounding contract applied to a reply, and it is deliberately
    independent of how the reply arrived: a citation of a slide that was never
    supplied is removed, a topic left with no slides is dropped, and a topic the
    model is unsure about is flagged rather than presented as settled.
    """
    supplied = {slide.index for slide in slides}
    proposals: list[TopicProposal] = []
    for claim in _claimed_topics(reply):
        title = _title(claim.get("title"))
        claimed = _claimed_slides(claim.get("slides"))
        cited = tuple(sorted(set(claimed) & supplied))
        if not title or not cited:
            logger.info(
                "dropped an ungrounded topic claim: %r citing %s", title, claimed
            )
            continue
        confidence = _confidence(claim.get("confidence"))
        proposals.append(
            TopicProposal(
                title=title,
                slides=cited,
                confidence=confidence,
                flag="" if confidence >= CONFIDENCE_FLOOR else UNSURE,
            )
        )
    proposals.sort(key=lambda proposal: proposal.slides[0])
    return proposals


def _decoded(reply: str) -> Any:
    """The JSON a model sent, with a Markdown code fence taken off it first.

    A model asked for JSON very often wraps the JSON in a fence, and the fence
    is not part of the answer - parsing the reply exactly as it arrived turns a
    correct reply into "not JSON", and drops a whole topic path or a whole quiz
    over three backticks. Found on the first real course in issue 14: Gemini
    answered a 233-slide topic request with fenced JSON, and nothing was taken
    from it.

    Only a reply that is entirely a fence is unwrapped. Prose around the JSON is
    something the model chose to say, and cutting down to the outermost braces
    would be guessing where the answer ends.
    """
    fenced = _FENCE.match(reply)
    return json.loads(fenced.group(1) if fenced else reply)


def _claimed_topics(reply: str) -> list[dict[str, Any]]:
    """The topic objects in a reply, or none at all if it is not one."""
    try:
        parsed = _decoded(reply)
    except json.JSONDecodeError:
        logger.warning("model reply was not JSON; no topics taken from it")
        return []
    if not isinstance(parsed, dict):
        logger.warning("model reply was not an object; no topics taken from it")
        return []
    claimed = parsed.get("topics")
    if not isinstance(claimed, list):
        logger.warning("model reply named no topics; no topics taken from it")
        return []
    return [claim for claim in claimed if isinstance(claim, dict)]


def _title(value: Any) -> str:
    """A topic's name, or nothing if the model did not give one.

    Only a string counts as a name. Coercing ``None`` to text would show the
    student a topic called "None", which reads as a title rather than as the
    absence of one.
    """
    return value.strip() if isinstance(value, str) else ""


def _claimed_slides(value: Any) -> list[int]:
    """The slide indices a claim cites, ignoring anything that is not one."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool)]


def _confidence(value: Any) -> float:
    """A confidence in 0..1, whatever the model wrote."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return min(1.0, max(0.0, float(value)))


@dataclass(frozen=True)
class SpanExcerpt:
    """One span as the model is allowed to see it, and as it is cited back.

    ``index`` is the span's place in the paragraphs handed over, which is both
    what the model cites and what the application turns back into the span it
    came from. Nothing else about the slide is shown: a question generated from
    a whole topic is a question generated from material the model was not shown,
    which is the failure this call is shaped to avoid.
    """

    index: int
    text: str


@dataclass(frozen=True)
class QuestionProposal:
    """One question the model wrote from one span, after the contract has been applied.

    ``answers`` is what counts as right, as text. For a multiple choice question
    that is exactly one entry and it is one of ``choices``, which is what lets
    the same grader mark both kinds and what stops a right answer from existing
    outside the options the student was offered.
    """

    span_index: int
    kind: str
    prompt: str
    choices: tuple[str, ...]
    answers: tuple[str, ...]


@dataclass(frozen=True)
class QuestionSet:
    """The questions that survived verification, and how many did not.

    ``dropped`` is counted rather than thrown away: the product promises an
    honest short quiz over a padded one, and a student told their quiz is two
    questions long needs to be told that two were left out rather than finding
    out by wondering.
    """

    questions: tuple[QuestionProposal, ...]
    dropped: int


#: How many candidates are put back to the model for verification, and how many
#: paragraphs are shown at all. Both bound one request: verification is one call
#: per candidate and a call can sit on a network for a minute, so an unbounded
#: number of them is a student whose page will not load. The limit is on the
#: work, not on the honesty - whatever is left is counted and shown as left out.
VERIFY_LIMIT = 8
SPAN_LIMIT = 40


def generate_questions(*, topic_title: str, spans: Sequence[SpanExcerpt]) -> QuestionSet:
    """Questions written from these spans alone, and checked against them.

    Span-first, in the order the contract demands: the model is shown one
    paragraph at a time and asked for a question about that paragraph, each
    answer is then put back to the model against the paragraph it came from,
    and only the answers that survive are returned. A question the paragraph
    does not support is dropped here and never reaches the student, because a
    question with a citation the citation does not support is worse than no
    question: it looks grounded and is not.

    ``dropped`` is every question written and not returned - ungrounded, of a
    kind that cannot be marked, failed verification, or written past the point
    this call will spend model time on. A count that only covered some of those
    would let the page claim nothing was left out on the paths where plenty was.
    """
    candidates, ungrounded = _grounded_questions(
        complete(_question_prompt(topic_title=topic_title, spans=spans)), spans=spans
    )
    by_index = {span.index: span for span in spans}
    verified: list[QuestionProposal] = []
    dropped = ungrounded
    for candidate in candidates:
        if len(verified) >= VERIFY_LIMIT:
            dropped += 1
            continue
        span = by_index[candidate.span_index]
        if not _answer_is_supported(span, candidate):
            logger.info(
                "dropped a question the span does not support: span %d %r",
                candidate.span_index,
                candidate.prompt,
            )
            dropped += 1
            continue
        verified.append(candidate)
    return QuestionSet(questions=tuple(verified), dropped=dropped)


def _question_prompt(*, topic_title: str, spans: Sequence[SpanExcerpt]) -> str:
    """The generation request as JSON: the paragraphs, numbered, and the rules.

    Sent as a document for the same reason the topic request is: a fake at
    ``complete`` reads the material exactly as a model does, so a test can drive
    the whole quiz through the seam and answer the way a model would.
    """
    return json.dumps(
        {
            "instruction": _QUESTION_INSTRUCTION,
            "topic": topic_title,
            "spans": [
                {"span": span.index, "text": span.text[:_SPAN_TEXT_LIMIT]}
                for span in spans[:SPAN_LIMIT]
            ],
        }
    )


def _verification_prompt(*, span: SpanExcerpt, question: QuestionProposal) -> str:
    """The check one answer gets against the one paragraph it came from."""
    return json.dumps(
        {
            "instruction": _VERIFY_INSTRUCTION,
            "span": {"span": span.index, "text": span.text[:_SPAN_TEXT_LIMIT]},
            "question": {
                "kind": question.kind,
                "prompt": question.prompt,
                "answers": list(question.answers),
            },
        }
    )


def _answer_is_supported(span: SpanExcerpt, question: QuestionProposal) -> bool:
    """Whether the paragraph itself says this is the answer.

    A reply that cannot be read as a yes is a no. The default of dropping is
    the whole point: a verification call that fails, answers something else, or
    times out leaves the student with a shorter quiz rather than with an
    unverified question wearing a citation.
    """
    try:
        reply = complete(_verification_prompt(span=span, question=question))
    except ModelUnavailable as failure:
        logger.info("verification call failed, dropping the question: %s", failure)
        return False
    return _supported(reply)


def _supported(reply: str) -> bool:
    """The yes or no out of a verification reply."""
    try:
        parsed = _decoded(reply)
    except json.JSONDecodeError:
        logger.warning("verification reply was not JSON; the question is dropped")
        return False
    return isinstance(parsed, dict) and parsed.get("supported") is True


def _grounded_questions(
    reply: str, *, spans: Sequence[SpanExcerpt]
) -> tuple[list[QuestionProposal], int]:
    """The questions a reply claims, kept only where they could be graded.

    The same contract as ``_grounded_topics`` applied to questions: a question
    citing a paragraph that was never supplied is dropped, a multiple choice
    question whose answer is not one of the options it offers is dropped
    because it cannot be marked without contradicting what the student saw, and
    a short answer the paragraph does not contain is dropped for the same
    reason - a grader comparing an answer against a phrase the material never
    used is not checking the student against their slides.

    Returns the questions and how many were refused, because a refusal the page
    cannot report is a question the student is not being told about.
    """
    supplied = {span.index: span for span in spans}
    proposals: list[QuestionProposal] = []
    dropped = 0
    for claim in _claimed_questions(reply):
        index = _span_index(claim.get("span"))
        prompt = _text(claim.get("prompt"))
        if index is None or index not in supplied or not prompt:
            logger.info("dropped an ungrounded question claim: %r on span %r", prompt, index)
            dropped += 1
            continue
        proposal = _graded_question(claim, prompt, index=index)
        if proposal is None:
            dropped += 1
            continue
        span = supplied[index]
        if proposal.kind == SHORT_ANSWER and not all(
            normalise(answer) in normalise(span.text) for answer in proposal.answers
        ):
            logger.info("dropped a question whose answer is not in its span: %r", prompt)
            dropped += 1
            continue
        proposals.append(proposal)
    return proposals, dropped


def _graded_question(
    claim: dict[str, Any], prompt: str, *, index: int
) -> QuestionProposal | None:
    """A claim as a question that can be marked automatically, or nothing.

    Only the two kinds the product grades are read. Anything else is refused
    rather than coerced: grading is multiple choice and short answer, and a
    third kind arriving from a model is not something to guess at here.
    """
    kind = claim.get("kind")
    if kind == MULTIPLE_CHOICE:
        choices = _distinct_texts(claim.get("choices"))
        answer = _text(claim.get("answer"))
        right = [choice for choice in choices if normalise(choice) == normalise(answer)]
        if len(choices) < 2 or not right or not normalise(right[0]):
            # An option that reduces to nothing - a row of dots, a stray
            # character - cannot be told apart from any other once normalised,
            # so a question built on it is one no student can ever be right
            # about.
            logger.info("dropped a multiple choice question with no answer in it: %r", prompt)
            return None
        return QuestionProposal(
            span_index=index,
            kind=MULTIPLE_CHOICE,
            prompt=prompt,
            choices=choices,
            answers=(answer,),
        )
    if kind == SHORT_ANSWER:
        answers = _distinct_texts(claim.get("answers"))
        if not answers:
            logger.info("dropped a short answer question with no answer: %r", prompt)
            return None
        return QuestionProposal(
            span_index=index,
            kind=SHORT_ANSWER,
            prompt=prompt,
            choices=(),
            answers=answers,
        )
    logger.info("dropped a question of an unknown kind %r: %r", kind, prompt)
    return None


def _claimed_questions(reply: str) -> list[dict[str, Any]]:
    """The question objects in a reply, or none at all if it is not one."""
    try:
        parsed = _decoded(reply)
    except json.JSONDecodeError:
        logger.warning("model reply was not JSON; no questions taken from it")
        return []
    if not isinstance(parsed, dict):
        logger.warning("model reply was not an object; no questions taken from it")
        return []
    claimed = parsed.get("questions")
    if not isinstance(claimed, list):
        logger.warning("model reply named no questions; no questions taken from it")
        return []
    return [claim for claim in claimed if isinstance(claim, dict)]


def _span_index(value: Any) -> int | None:
    """The span a claim cites, or nothing if it did not cite one properly."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _distinct_texts(value: Any) -> tuple[str, ...]:
    """The strings a claim offers, in the order given, with blanks and repeats out.

    Duplicates are dropped rather than kept: a multiple choice question with the
    right answer written twice is a question whose answer the student can pick
    wrongly and still be told they are right.
    """
    if not isinstance(value, list):
        return ()
    seen: set[str] = set()
    kept: list[str] = []
    for item in value:
        text = _text(item)
        key = normalise(text)
        if text and key not in seen:
            seen.add(key)
            kept.append(text)
    return tuple(kept)


def _text(value: Any) -> str:
    """A string the model wrote, trimmed, or nothing at all."""
    return value.strip() if isinstance(value, str) else ""


def normalise(text: str) -> str:
    """Two strings that mean the same thing, compared without case or punctuation.

    Used both to check an answer against the span it cites and to mark a
    student's answer, so that the answer a question was verified against and
    the answer that counts as right are compared the same way. Without that,
    grading could contradict the very text the question was checked against -
    a verified answer written ``2.`` in the span and typed ``2`` by the student
    would be marked wrong.

    Folds the compatibility forms onto their plain letters first, so the same
    word compares the same however it was written: a subscript ``H₂O`` over a
    PDF and an ``H2O`` typed by hand both fold to ``h2o`` (NFKD), and the same
    goes for a full-width form. A word in a non-Latin script then stays a whole
    word: a combining mark takes its place in the word it belongs to instead of
    reading as a break, so a Devanagari matra does not split its own word in
    half. Everything the eye cannot see or the ear cannot read - invisible
    format characters included - is still where one word ends and another
    begins, because a fold that glued "not" to the word it was denying would
    read a refusal as an agreement. Punctuation and case are still folded
    away, so "the null space." still compares as "the null space".
    """
    folded = unicodedata.normalize(
        "NFKD", unicodedata.normalize("NFKD", text).casefold()
    )

    def folded_form(char: str) -> str:
        category = unicodedata.category(char)
        if char.isalnum() or category.startswith("M"):
            return char
        return " "

    return " ".join("".join(folded_form(char) for char in folded).split())


def complete(prompt: str) -> str:
    """Send one prompt to the configured model and return its reply.

    The only function in Mastory that leaves the machine for a model, and the
    one place a deterministic fake is installed: everything above it, including
    the whole HTTP test suite, goes through this call.

    Every call is counted as it finishes, whether it answered or failed, because
    a refused call is the expensive one: it took the wall clock and the
    provider's money and gave back nothing, and a ledger that only counted the
    calls which came back would price a failing week at nothing.

    A provider that is throttling us is waited for rather than given up on.
    One real course in issue 14 needed about fifty calls inside a few minutes,
    which is more than the free tier allows per minute; read as a plain failure
    that throttling dropped every question in the second half of the course and
    the student was told the model could not be reached.

    What the ledger counts is HTTP attempts, not intentions: a call throttled
    four times and answered on the fifth is five calls, because five requests
    were made and throttling is counted against a provider's quota.
    """
    base = settings.MODEL_BASE_URL
    if not base or not settings.MODEL_NAME:
        raise ModelUnavailable(*NOT_CONFIGURED)
    waited = 0.0
    begun = time.monotonic()
    for attempt in range(THROTTLE_ATTEMPTS):
        request = urllib.request.Request(  # noqa: S310 - a configured http(s) endpoint
            f"{base.rstrip('/')}/chat/completions",
            data=json.dumps(
                {
                    "model": settings.MODEL_NAME,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.MODEL_API_KEY}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(  # noqa: S310
                request, timeout=settings.MODEL_TIMEOUT_SECONDS
            ) as response:
                body: Any = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as failure:
            note_call(None)
            # The error is file-like and can hold the response it came from, so
            # it is closed here rather than left for the collector: a throttled
            # run makes these by the dozen.
            failure.close()
            throttled = failure.code == THROTTLED
            wait = _backoff(failure, attempt, waited) if throttled else 0.0
            out_of_time = (
                time.monotonic() - begun + wait
            ) > RETRY_BUDGET_SECONDS
            if not throttled or out_of_time or attempt == THROTTLE_ATTEMPTS - 1:
                raise ModelUnavailable(
                    *(THROTTLE_MESSAGE if throttled else UNREACHABLE)
                ) from failure
            time.sleep(wait)
            waited += wait
            continue
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError,
        ) as failure:
            note_call(None)
            raise ModelUnavailable(*UNREACHABLE) from failure
        note_call(_tokens(body))
        return _reply_text(body)
    raise ModelUnavailable(*UNREACHABLE)  # pragma: no cover - the loop returns or raises


def _backoff(failure: urllib.error.HTTPError, attempt: int, waited: float) -> float:
    """How long to wait before trying a throttled call again, in whole seconds.

    The provider's own ``Retry-After`` is believed where it gives one, in either
    of the two forms the header is allowed to take - seconds, or an HTTP date -
    because it is the only number here that knows how loaded the provider
    actually is. The exponential term is the floor under it, so a provider that
    sends no header at all is still waited for rather than hammered.

    What bounds it is the total already waited, not this wait alone: a provider
    asking for a minute on every refusal would otherwise buy four minutes of a
    student watching a spinner, which is longer than the page asks of them. The
    last wait is clamped so the total never passes ``MAX_BACKOFF_SECONDS``.
    """
    return min(
        max(_asked_for(failure), min(2.0**attempt, MAX_BACKOFF_SECONDS)),
        max(MAX_BACKOFF_SECONDS - waited, 0.0),
    )


def _asked_for(failure: urllib.error.HTTPError) -> float:
    """How many seconds the provider asked us to wait, or zero where it did not."""
    given = failure.headers.get("Retry-After") if failure.headers else None
    if given is None:
        return 0.0
    try:
        seconds = float(given)
    except (TypeError, ValueError):
        pass
    else:
        # ``nan`` is a float and comparing it silently answers False everywhere,
        # which would carry a nan into ``sleep`` and raise there instead.
        if seconds == seconds and 0 <= seconds < float("inf"):
            return min(seconds, MAX_BACKOFF_SECONDS)
        return 0.0
    try:
        when = parsedate_to_datetime(given)
    except (TypeError, ValueError):
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return min(max((when - datetime.now(when.tzinfo)).total_seconds(), 0.0), MAX_BACKOFF_SECONDS)


def _tokens(body: Any) -> int | None:
    """What the reply says this call moved, or nothing where it says nothing.

    The whole of the provider's count rather than what went in and what came back
    separately, because the rate Mastory prices at is one number and half of one
    would be a rate we had invented.
    """
    try:
        total = body["usage"]["total_tokens"]
    except (KeyError, IndexError, TypeError):
        return None
    if isinstance(total, bool) or not isinstance(total, int):
        return None
    return total


class Meter:
    """What the model calls made inside one piece of work came to.

    Counted here, in the one function that knows a call was made, rather than by
    the caller wrapping ``complete``: the test suite fakes exactly that function,
    so anything counting calls from outside it would be counting the fake and
    nothing else, and the one piece of this product that cannot be reconstructed
    afterwards would be the one piece nothing ever exercised.

    The wall clock is not in here. Reading a PDF is not work a model did and
    still has to be counted, so the seconds a piece of work took are measured
    around the whole of it, in ``material.spend``.
    """

    def __init__(self) -> None:
        self.calls = 0
        self._moved = 0
        self._said = 0

    def note(self, tokens: int | None) -> None:
        """One call, and what it said it moved. Nothing where it said nothing."""
        self.calls += 1
        if tokens is not None:
            self._moved += tokens
            self._said += 1

    @property
    def tokens(self) -> int | None:
        """What the calls moved between them, or nothing while one would not say.

        All or nothing rather than the sum of whatever arrived: half a quiz's
        calls reporting is not half a quiz's cost, and a money figure printed
        from the calls that happened to answer would be one this product cannot
        support. Zero where nothing was called at all, which is a different fact
        again - there is nothing for work no model did to have failed to report.
        """
        if not self.calls:
            return 0
        return self._moved if self._said == self.calls else None


#: Where the calls being made right now are counted. Holding a meter nobody is
#: reading is the state a call is in outside ``material.spend.working``, and every
#: model call in Mastory is made inside one, so that call has no row to land on.
_counting: ContextVar[Meter] = ContextVar("mastory_meter", default=Meter())


@contextmanager
def measuring() -> Iterator[Meter]:
    """Meter the calls made inside this block, and hand back what they came to."""
    meter = Meter()
    held = _counting.set(meter)
    try:
        yield meter
    finally:
        _counting.reset(held)


def note_call(tokens: int | None) -> None:
    """What one call moved, told to the meter around us.

    Told rather than returned because ``complete`` answers with the reply the
    student is shown and nothing else, and because a model stands in for the
    provider in the tests: saying what a call moved is part of being a model, so
    a fake that reported nothing would leave every cost in the suite at zero and
    the priced figure unexercised.
    """
    _counting.get().note(tokens)


NOT_CONFIGURED = (
    "This server has no language model configured.",
    "Add the topics yourself below, or ask whoever runs the server to set "
    "MODEL_BASE_URL and MODEL_NAME.",
)
UNREACHABLE = (
    "The language model could not be reached.",
    "Try again in a minute. You can add the topics yourself below in the "
    "meantime.",
)
THROTTLE_MESSAGE = (
    "The language model is too busy to answer right now.",
    "Wait a minute and try again. You can add the topics yourself below in the "
    "meantime.",
)


def _reply_text(body: Any) -> str:
    """The reply text out of a chat completion, or a failure that says so."""
    try:
        reply = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as failure:
        raise ModelUnavailable(*UNREACHABLE) from failure
    if not isinstance(reply, str):
        raise ModelUnavailable(*UNREACHABLE)
    return reply