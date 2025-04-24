class Error(Exception):
    """Base class for exceptions.

    Raise :obj:`Error` (or a subclass) for known errors where we can provide a meaningful error message.

    :param message: Error message for the user."""

    def __init__(self, message):
        self.message = message  #: Error message for users.

class ConfigError(Error):
    """Subclass to signal errors in the configuration or input files provided by the user.

    :obj:`ConfigError` contains a reference to the config section where the problem is found.  This can be used to
    tell the user which section of the configuration should be changed, or, when using a GUI, which input widget they
    should look at.  For example if there is a problem with the configuration value ``config['input']['maps'][
    'land_use']``, :attr:`ConfigError.path` should have the value ``['input', 'maps', 'land_use']``.

    :param message: Error message.
    :param path: List of keys pointing to the config
    """

    def __init__(self, message, path):
        super().__init__(message)
        self.path = path  #: List of configuration keys pointing to the config value which caused the error.

