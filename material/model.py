"""The one place Mastory talks to a language model.

Every model call in the application goes through this module, and all of them
obey one grounding contract:

    A call is given the student's own material and nothing else, and what comes
    back has to point at the material it came from.

The contract is enforced here rather than asked for in a prompt. A reply that
cites a slide the caller never supplied loses that citation, and a reply with
nothing left to point at is dropped rather than shown. A reply that is unsure
about itself comes back flagged, so the student is asked to check it instead of
being handed a confident guess. Later features need more from a model -
questions, verification - and they ask here too, so there is exactly one place
where "grounded in this student's slides" is decided.

Nothing above this module knows how a model is reached, and nothing in it knows
what a topic is.
"""

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

#: Below this confidence the student is told the topic needs checking against
#: their own slides. Above it, the topic is shown as inferred and unremarkable.
CONFIDENCE_FLOOR = 0.6

#: How much of one slide the model is shown. Long slides are trimmed rather than
#: dropped, so a topic can still be inferred from the part that was sent.
_SLIDE_TEXT_LIMIT = 1200

_INSTRUCTION = (
    "You are reading one student's own course material to work out the topic "
    "path of their course. Use only the slides below; do not use outside "
    "knowledge and do not invent slides.\n"
    "Return one topic per idea the course teaches, in the order the course "
    "teaches them. A topic may cover slides that are not next to each other, "
    "and its 'slides' must list the index of every slide that covers it.\n"
    "Set confidence low when the slides do not clearly support the topic or its "
    "extent; the student is shown that low confidence and asked to check it.\n"
    'Reply with JSON only: {"topics": [{"title": "...", "slides": [1, 2], '
    '"confidence": 0.0}]}'
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
    topics by.
    """

    index: int
    title: str
    text: str


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
    return grounded_topics(reply, slides=slides)


def _topic_prompt(*, course_title: str, slides: Sequence[SlideExcerpt]) -> str:
    """The whole request as JSON: the material, and what to do with it.

    Sent as one document rather than as prose so that what the model was shown
    is readable in a log line, and so a fake at ``complete`` can read the
    material the same way a model does.
    """
    return json.dumps(
        {
            "instruction": _INSTRUCTION,
            "course": course_title,
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


def grounded_topics(reply: str, *, slides: Sequence[SlideExcerpt]) -> list[TopicProposal]:
    """The topics a reply claims, kept only where they point at real material.

    This is the grounding contract applied to a reply, and it is deliberately
    independent of how the reply arrived: a citation of a slide that was never
    supplied is removed, a topic left with no slides is dropped, and a topic the
    model is unsure about is flagged rather than presented as settled.
    """
    supplied = {slide.index for slide in slides}
    proposals: list[TopicProposal] = []
    for claim in _claimed_topics(reply):
        title = str(claim.get("title", "")).strip()
        slides_claimed = _claimed_slides(claim.get("slides"))
        cited = tuple(sorted(index for index in slides_claimed if index in supplied))
        if not title or not cited:
            logger.info(
                "dropped an ungrounded topic claim: %r citing %s", title, slides_claimed
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


#: What the student is told about a topic the model was unsure of.
UNSURE = "The model was not sure of this topic. Check it against your slides."


def _claimed_topics(reply: str) -> list[dict[str, Any]]:
    """The topic objects in a reply, or none at all if it is not one."""
    try:
        parsed = json.loads(reply)
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


def _claimed_slides(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool)]


def _confidence(value: Any) -> float:
    """A confidence in 0..1, whatever the model wrote."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return min(1.0, max(0.0, float(value)))


def complete(prompt: str) -> str:
    """Send one prompt to the configured model and return its reply.

    The only function in Mastory that leaves the machine for a model, and the
    one place a deterministic fake is installed: everything above it, including
    the whole HTTP test suite, goes through this call.
    """
    base = settings.MODEL_BASE_URL
    if not base or not settings.MODEL_NAME:
        raise ModelUnavailable(*NOT_CONFIGURED)
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
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as failure:
        raise ModelUnavailable(*UNREACHABLE) from failure
    return _reply_text(body)


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


def _reply_text(body: Any) -> str:
    """The reply text out of a chat completion, or a failure that says so."""
    try:
        reply = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as failure:
        raise ModelUnavailable(*UNREACHABLE) from failure
    if not isinstance(reply, str):
        raise ModelUnavailable(*UNREACHABLE)
    return reply