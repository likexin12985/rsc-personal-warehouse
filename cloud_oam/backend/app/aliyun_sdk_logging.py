"""Keep Alibaba SDK wire and credential diagnostics out of application logs."""

import logging


def silence_aliyun_sdk_loggers() -> None:
    # Tea and the credential SDK may install DEBUG stream handlers of their
    # own. Provider failures are mapped to fixed public error codes instead.
    for logger_name in ("credentials", "alibabacloud-tea"):
        sdk_logger = logging.getLogger(logger_name)
        sdk_logger.handlers.clear()
        sdk_logger.addHandler(logging.NullHandler())
        sdk_logger.propagate = False
        sdk_logger.disabled = True
