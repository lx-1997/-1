"""Keep seeded story dates and implicit query clocks in the same test world."""
from datetime import datetime
from deepfocus_api import story_engine


def freeze_story_clock(monkeypatch, reference):
    clock = {'now': reference}
    class FrozenMeta(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)
    class FrozenDateTime(datetime, metaclass=FrozenMeta):
        @classmethod
        def now(cls, tz=None):
            value = clock['now']
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)
    monkeypatch.setattr(story_engine, 'datetime', FrozenDateTime)
    return clock
