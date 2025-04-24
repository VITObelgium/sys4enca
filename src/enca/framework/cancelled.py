class Cancelled(Exception):
    """Custom Exception to signal cancellation of a Run.

    This Exception not raised by the package itself.  Rather, it is "injected" into the thread of a running calculation
    by the QGIS plugin when the user clicks the cancel button.
    """

    pass