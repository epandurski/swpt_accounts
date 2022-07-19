import os
import warnings
import asyncio
import requests
import aiohttp
from sqlalchemy.exc import SAWarning
from werkzeug.local import Local, LocalProxy
from flask import current_app
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_signalbus import SignalBusMixin, AtomicProceduresMixin, rabbitmq
from flask_melodramatiq import RabbitmqBroker
from dramatiq import Middleware

MAIN_EXCHANGE_NAME = 'dramatiq'
APP_QUEUE_NAME = os.environ.get('APP_QUEUE_NAME', 'swpt_accounts')

_local = Local()


warnings.filterwarnings(
    'ignore',
    r"this is a regular expression for the text of the warning",
    SAWarning,
)


class CustomAlchemy(AtomicProceduresMixin, SignalBusMixin, SQLAlchemy):
    pass


class EventSubscriptionMiddleware(Middleware):
    @property
    def actor_options(self):
        return {'event_subscription'}


def get_asyncio_loop():
    if not hasattr(_local, 'asyncio_loop'):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:  # pragma: nocover
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        _local.asyncio_loop = loop

    return _local.asyncio_loop


def get_aiohttp_session():
    if not hasattr(_local, 'aiohttp_session'):
        connector = aiohttp.TCPConnector(
            limit=current_app.config['APP_FETCH_CONNECTIONS'],
            ttl_dns_cache=int(current_app.config['APP_FETCH_DNS_CACHE_SECONDS']),
        )
        timeout = aiohttp.ClientTimeout(total=current_app.config['APP_FETCH_API_TIMEOUT_SECONDS'])
        session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        _local.aiohttp_session = session

    return _local.aiohttp_session


def get_requests_session():
    if not hasattr(_local, 'requests_session'):
        session = requests.Session()
        session.timeout = current_app.config['APP_FETCH_API_TIMEOUT_SECONDS']
        _local.requests_session = session

    return _local.requests_session


db = CustomAlchemy()
db.signalbus.autoflush = False
migrate = Migrate()
asyncio_loop = LocalProxy(get_asyncio_loop)
aiohttp_session = LocalProxy(get_aiohttp_session)
requests_session = LocalProxy(get_requests_session)
protocol_broker = RabbitmqBroker(config_prefix='PROTOCOL_BROKER', confirm_delivery=True)
protocol_broker.add_middleware(EventSubscriptionMiddleware())
chores_broker = RabbitmqBroker(config_prefix='CHORES_BROKER', confirm_delivery=False)
publisher = rabbitmq.Publisher(url_config_key='PROTOCOL_BROKER_URL')
