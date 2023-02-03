class Error(Exception):
    """Base class for exceptions.

    Raise :obj:`Error` (or a subclass) for known errors where we can provide a meaningful error message.

    :param message: Error message for the user."""

    def __init__(self, message):
        self.message = message  #: Error message for users.
