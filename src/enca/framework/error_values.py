"""List of sys.exit() values for the ENCA invocation via command-line interface."""

# os.EX_* tries to standardize the exit values, but does not work cross-platform

# processing was completed successfully
RUN_OK           = 0
# software error
ERROR_PROCESSING = 1
# config value error
ERROR_CONFIG     = 2
# other software errors
ERROR_OTHER      = 3
# run completed with warnings
RUN_WARN         = 4
# user-cancelled the run
CANCEL           = 5
