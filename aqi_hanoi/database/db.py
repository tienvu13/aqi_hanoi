from sqlalchemy import create_engine

from aqi_hanoi.core.settings import get_database_url


engine = create_engine(get_database_url())
