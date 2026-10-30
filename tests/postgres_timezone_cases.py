"""Reviewed 2026f cases shared by GNU date and PostgreSQL SQL probes."""

TZDATA_RELEASE = '2026f'
VERSION = TZDATA_RELEASE + '-0+deb12u1'
OFFSETS = {'Etc/UTC': 0, 'Europe/Moscow': 10800,
           'America/Winnipeg': -18000, 'America/Yellowknife': -21600}

# Preserve the previous UTC/Moscow/Canada checks. Cuba omits the November 2026
# fall-back, then resumes the scheduled fall-back on 7 November 2027.
DATE_CASES = tuple((zone, instant, offset) for zone, offset in OFFSETS.items()
                   for instant in ('2026-11-02T12:00:00Z', '2030-01-15T12:00:00Z'))
DATE_CASES += tuple((zone, instant, offset) for zone in ('America/Havana', 'Cuba')
                    for instant, offset in (
                        ('2026-11-01T04:59:59Z', -14400),
                        ('2026-11-01T05:00:00Z', -14400),
                        ('2026-11-02T12:00:00Z', -14400),
                        ('2027-01-15T12:00:00Z', -14400),
                        ('2027-11-07T04:59:59Z', -14400),
                        ('2027-11-07T05:00:00Z', -18000),
                        ('2027-11-08T12:00:00Z', -18000),
                        ('2030-01-15T12:00:00Z', -18000),
                    ))


def offset_text(seconds):
    minutes = abs(seconds) // 60
    return ('-' if seconds < 0 else '+') + f'{minutes // 60:02}{minutes % 60:02}'
