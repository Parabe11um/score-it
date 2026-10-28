"""Intentional image startup contracts, separate from vendor defaults."""
APP_HEALTHCHECK={
    'Test':['CMD','python','-c',"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz/', timeout=5).read()"],
    'Interval':15000000000,'Timeout':7000000000,'StartPeriod':20000000000,'Retries':5}
PG_HEALTHCHECK={
    'Test':['CMD-SHELL','pg_isready -h 127.0.0.1 -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}"'],
    'Interval':10000000000,'Timeout':5000000000,'StartPeriod':20000000000,'Retries':5}


def health_matches(actual,expected):
    return (isinstance(actual,dict) and all(actual.get(k)==v for k,v in expected.items())
            and set(actual)<=set(expected)|{'StartInterval'}
            and actual.get('StartInterval',0) in (0,5000000000))
