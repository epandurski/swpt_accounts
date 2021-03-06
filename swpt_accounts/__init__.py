__version__ = '0.1.0'

import logging
import sys
import os
import os.path
from typing import List
from flask_melodramatiq import missing


def configure_logging(level: str, format: str, associated_loggers: List[str]) -> None:  # pragma: no cover
    root_logger = logging.getLogger()

    # Setup the root logger's handler if necessary.
    if not root_logger.hasHandlers():
        handler = logging.StreamHandler(sys.stdout)
        fmt = '%(asctime)s:%(levelname)s:%(name)s:%(message)s'

        if format == 'text':
            handler.setFormatter(logging.Formatter(fmt))
        elif format == 'json':
            from pythonjsonlogger import jsonlogger
            handler.setFormatter(jsonlogger.JsonFormatter(fmt))
        else:
            raise RuntimeError(f'invalid log format: {format}')

        root_logger.addHandler(handler)

    # Set the log level for this app's logger.
    app_logger = logging.getLogger(__name__)
    app_logger.setLevel(level.upper())
    app_logger_level = app_logger.getEffectiveLevel()

    # Make sure that all loggers that are associated to this app have
    # their log levels set to the specified level as well.
    for qualname in associated_loggers:
        logging.getLogger(qualname).setLevel(app_logger_level)

    # Make sure that the root logger's log level (that is: the log
    # level for all third party libraires) is not lower than the
    # specified level.
    if app_logger_level > root_logger.getEffectiveLevel():
        root_logger.setLevel(app_logger_level)

    # Delete all gunicorn's log handlers (they are not needed in a
    # docker container because everything goes to the stdout anyway),
    # and make sure that the gunicorn logger's log level is not lower
    # than the specified level.
    gunicorn_logger = logging.getLogger('gunicorn.error')
    gunicorn_logger.propagate = True
    for h in gunicorn_logger.handlers:
        gunicorn_logger.removeHandler(h)
    if app_logger_level > gunicorn_logger.getEffectiveLevel():
        gunicorn_logger.setLevel(app_logger_level)


class MetaEnvReader(type):
    def __init__(cls, name, bases, dct):
        """MetaEnvReader class initializer.

        This function will get called when a new class which utilizes
        this metaclass is defined, as opposed to when an instance is
        initialized. This function overrides the default configuration
        from environment variables.

        """

        super().__init__(name, bases, dct)
        NoneType = type(None)
        annotations = dct.get('__annotations__', {})
        falsy_values = {'false', 'off', 'no', ''}
        for key, value in os.environ.items():
            if hasattr(cls, key):
                target_type = annotations.get(key) or type(getattr(cls, key))
                if target_type is NoneType:  # pragma: no cover
                    target_type = str

                if target_type is bool:
                    value = value.lower() not in falsy_values
                else:
                    value = target_type(value)

                setattr(cls, key, value)


class Configuration(metaclass=MetaEnvReader):
    SECRET_KEY = 'dummy-secret'
    SQLALCHEMY_DATABASE_URI = ''
    SQLALCHEMY_POOL_SIZE: int = None
    SQLALCHEMY_POOL_TIMEOUT: int = None
    SQLALCHEMY_POOL_RECYCLE: int = None
    SQLALCHEMY_MAX_OVERFLOW: int = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False
    PROTOCOL_BROKER_URL = 'amqp://guest:guest@localhost:5672'
    CHORES_BROKER_CLASS = 'RabbitmqBroker'
    CHORES_BROKER_URL: str = missing
    APP_PROCESS_BALANCE_CHANGES_THREADS = 1
    APP_PROCESS_BALANCE_CHANGES_WAIT = 5.0
    APP_PROCESS_BALANCE_CHANGES_MAX_COUNT = 500000
    APP_PROCESS_TRANSFER_REQUESTS_THREADS = 1
    APP_PROCESS_TRANSFER_REQUESTS_WAIT = 5.0
    APP_PROCESS_TRANSFER_REQUESTS_MAX_COUNT = 500000
    APP_PROCESS_FINALIZATION_REQUESTS_THREADS = 1
    APP_PROCESS_FINALIZATION_REQUESTS_WAIT = 5.0
    APP_PROCESS_FINALIZATION_REQUESTS_MAX_COUNT = 500000
    APP_FLUSH_REJECTED_TRANSFERS_BURST_COUNT = 10000
    APP_FLUSH_PREPARED_TRANSFERS_BURST_COUNT = 10000
    APP_FLUSH_FINALIZED_TRANSFERS_BURST_COUNT = 10000
    APP_FLUSH_ACCOUNT_TRANSFERS_BURST_COUNT = 10000
    APP_FLUSH_ACCOUNT_UPDATES_BURST_COUNT = 10000
    APP_FLUSH_ACCOUNT_PURGES_BURST_COUNT = 10000
    APP_FLUSH_REJECTED_CONFIGS_BURST_COUNT = 10000
    APP_FLUSH_PENDING_BALANCE_CHANGES_BURST_COUNT = 10000
    APP_ACCOUNTS_SCAN_HOURS = 8.0
    APP_PREPARED_TRANSFERS_SCAN_DAYS = 1.0
    APP_INTRANET_EXTREME_DELAY_DAYS = 14.0
    APP_SIGNALBUS_MAX_DELAY_DAYS = 7.0
    APP_ACCOUNT_HEARTBEAT_DAYS = 7.0
    APP_PREPARED_TRANSFER_REMAINDER_DAYS = 7.0
    APP_PREPARED_TRANSFER_MAX_DELAY_DAYS = 30.0
    APP_FETCH_API_URL: str = None
    APP_FETCH_API_TIMEOUT_SECONDS = 5.0
    APP_FETCH_DNS_CACHE_SECONDS = 10.0
    APP_FETCH_CONNECTIONS = 100
    APP_FETCH_DATA_CACHE_SIZE = 1000
    APP_MIN_INTEREST_CAPITALIZATION_DAYS = 14.0
    APP_MAX_INTEREST_TO_PRINCIPAL_RATIO = 0.0001
    APP_DELETION_ATTEMPTS_MIN_DAYS = 14.0
    APP_ACCOUNTS_SCAN_BLOCKS_PER_QUERY = 40
    APP_ACCOUNTS_SCAN_BEAT_MILLISECS = 25
    APP_PREPARED_TRANSFERS_SCAN_BLOCKS_PER_QUERY = 40
    APP_PREPARED_TRANSFERS_SCAN_BEAT_MILLISECS = 25


def _check_config_sanity(c):  # pragma: nocover
    if (c['APP_PREPARED_TRANSFER_MAX_DELAY_DAYS'] < c['APP_SIGNALBUS_MAX_DELAY_DAYS']):
        raise RuntimeError(
            'The configured value for APP_PREPARED_TRANSFER_MAX_DELAY_DAYS is too '
            'small compared to the configured value for APP_SIGNALBUS_MAX_DELAY_DAYS. This '
            'may result in frequent timing out of prepared transfers due to message '
            'delays. Choose more appropriate configuration values.'
        )

    if not 0.0 < c['APP_MAX_INTEREST_TO_PRINCIPAL_RATIO'] <= 0.10:
        raise RuntimeError(
            'The configured value for APP_MAX_INTEREST_TO_PRINCIPAL_RATIO is outside '
            'of the interval that is good for practical use. Choose a more appropriate '
            'value.'
        )

    if c['APP_MIN_INTEREST_CAPITALIZATION_DAYS'] > 92:
        raise RuntimeError(
            'The configured value for APP_MIN_INTEREST_CAPITALIZATION_DAYS is too '
            'big. This may result in quirky capitalization of the accumulated '
            'interest. Choose a more appropriate value.'
        )

    if c['APP_ACCOUNTS_SCAN_HOURS'] > 48:
        raise RuntimeError(
            'The configured value for APP_ACCOUNTS_SCAN_HOURS is too big. This'
            'may result in lagging account status updates. Choose a more appropriate '
            'value.'
        )


def create_app(config_dict={}):
    from werkzeug.middleware.proxy_fix import ProxyFix
    from flask import Flask
    from swpt_lib.utils import Int64Converter
    from .extensions import db, migrate, protocol_broker, chores_broker
    from .routes import fetch_api
    from .cli import swpt_accounts
    from . import models  # noqa

    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_port=1)
    app.url_map.converters['i64'] = Int64Converter
    app.config.from_object(Configuration)
    app.config.from_mapping(config_dict)
    db.init_app(app)
    migrate.init_app(app, db)
    protocol_broker.init_app(app)
    chores_broker.init_app(app)
    app.register_blueprint(fetch_api)
    app.cli.add_command(swpt_accounts)
    _check_config_sanity(app.config)

    return app


configure_logging(
    level=os.environ.get('APP_LOG_LEVEL', 'warning'),
    format=os.environ.get('APP_LOG_FORMAT', 'text'),
    associated_loggers=os.environ.get('APP_ASSOCIATED_LOGGERS', '').split(),
)
