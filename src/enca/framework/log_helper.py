import os
import platform
import logging

# set up logging for the top level of the package (enca or enca_plugin)
_package_name = __name__.split('.')[0]
# lineno can be missing, default value can only be set in Python >= 3.10
#_log_formatter = logging.Formatter('%(asctime)s %(name)s %(lineno)d [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M')
_log_formatter = logging.Formatter('%(asctime)s %(name)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M')

# activate the py.warnings Logger that captures Python warnings
logging.captureWarnings(True)

def set_up_console_logging_for_logger(logger_name: str, verbose: bool = False) -> None:
    """Install a log handler that prints to the terminal.

    :param logger_name: name of logger object
    :param verbose: boolean if verbose logging should be printed
    """
    logger=logging.getLogger(logger_name)
    ch = logging.StreamHandler()
    ch.setFormatter(_log_formatter)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(ch)
    if verbose:
        logger.info('Started verbose logging to console')
    else:
        logger.info('Started non-verbose logging to console')

def set_up_log_file_for_logger(logger_name: str, log_dir: str = None, verbose: bool = False, filename:str = None) -> None:
    """Install a log handler that prints to a file in the provided directory.

    :param logger_name: name of logger object
    :param log_dir: absolute folder name in which the log file is generated
    :param verbose: boolean if verbose logging is entered in the log file
    :param filename: base name for the logfile
    """
    if log_dir is None:
        log_dir = get_log_location(_package_name)
    if filename is None:
        filename=f'{_package_name}.log'
    logger=logging.getLogger(logger_name)
    log_file = os.path.join(log_dir, filename)
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh.setFormatter(_log_formatter)
    logger.addHandler(fh)
    if verbose:
        logger.info('Started verbose logging to file ' + filename)
    else:
        logger.info('Started non-verbose logging to file ' + filename)


def remove_log_file_handlers_from_logger(logger_name: str) -> None:
    """Remove the file handlers from a logger object."""
    logger = logging.getLogger(logger_name)
    for ha in logger.handlers:
        if isinstance(ha, logging.FileHandler):
            logger.removeHandler(ha)

def has_filehandler(logger_name: str) -> bool:
    """ Check if file logging was set up """
    logger = logging.getLogger(logger_name)
    has_file_handler = False
    for ha in logger.handlers:
        if isinstance(ha, logging.FileHandler):
            has_file_handler = True
            break
            
    return has_file_handler

def set_log_verbosity(logger_name: str, verbose: bool = False):
    logger = logging.getLogger(logger_name)
    for ha in logger.handlers:
        ha.setLevel(logging.DEBUG if verbose else logging.INFO)
    
def get_log_location(app_name: str = None) -> str:
    """Get an appropriate user directory to write the enca log file.

    On windows, this should be somewhere in AppData, on Linux it should be a directory in the user's $HOME."""
    system = platform.system()
    
    if app_name is None:
        app_name = _package_name        

    if system == 'Windows':
        log_dir = os.path.join(os.getenv('APPDATA'), app_name)
    elif system == 'Linux':
        # Following https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html :
        log_dir = os.getenv('XDG_STATE_HOME')
        if log_dir is None:
            log_dir = os.path.join(os.getenv('HOME'), '.local', 'state', '.'+app_name)
    else:  # Mac?
        log_dir = os.path.join(os.getenv('HOME'), '.'+app_name)

    os.makedirs(log_dir, exist_ok=True)
    return log_dir    
