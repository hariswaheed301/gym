class ServiceError(Exception):
    """A business-rule violation whose message is safe to show to staff."""


class AccessDenied(ServiceError):
    def __init__(self, decision):
        self.decision = decision
        super().__init__(decision.message)


class AlreadyInside(ServiceError):
    pass


class NotInside(ServiceError):
    pass
