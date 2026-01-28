import logging
import sys

# Source - https://stackoverflow.com/a
# Posted by airmind, modified by community. See post 'Timeline' for change history
# Retrieved 2025-12-02, License - CC BY-SA 4.0

BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(8)

# The background is set with 40 plus the number of the color, and the foreground with 30

# These are the sequences need to get colored ouput
RESET_SEQ = "\033[0m"
COLOR_SEQ = "\033[1;%dm"
BOLD_SEQ = "\033[1m"


def formatter_message(message, use_color=True):
    if use_color:
        message = message.replace("$RESET", RESET_SEQ).replace("$BOLD", BOLD_SEQ)
    else:
        message = message.replace("$RESET", "").replace("$BOLD", "")
    return message


COLORS = {
    'WARNING': YELLOW,
    'INFO': BLUE,
    'DEBUG': WHITE,
    'CRITICAL': YELLOW,
    'ERROR': RED
}


class ColoredFormatter(logging.Formatter):
    def __init__(self, msg, use_color=True):
        logging.Formatter.__init__(self, msg)
        self.use_color = use_color

    def format(self, record):
        levelname = record.levelname
        if self.use_color and levelname in COLORS:
            levelname_color = COLOR_SEQ % (30 + COLORS[levelname]) + levelname + RESET_SEQ
            record.levelname = levelname_color
        return logging.Formatter.format(self, record)


# Source - https://stackoverflow.com/a
# Posted by airmind, modified by community. See post 'Timeline' for change history
# Retrieved 2025-12-02, License - CC BY-SA 4.0

# Custom logger class with multiple destinations
class ColoredLogger(logging.Logger):
    FORMAT = "$BOLD%(name)s$RESET | %(levelname)-18s | $BOLD%(filename)s$RESET:%(lineno)d - %(message)s"
    COLOR_FORMAT = formatter_message(FORMAT, True)

    def __init__(self, name):
        logging.Logger.__init__(self, name, logging.DEBUG)

        color_formatter = ColoredFormatter(self.COLOR_FORMAT)

        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(color_formatter)

        self.addHandler(console)
        return
    
    def debug(self, *args):
        super().debug(" ".join(map(str, args)), stacklevel=2)

    def info(self, *args) -> None:
        super().info(" ".join(map(str, args)), stacklevel=2)

    def warning(self, *args) -> None:
        super().warning(" ".join(map(str, args)), stacklevel=2)

    def error(self, *args) -> None:
        super().error(" ".join(map(str, args)), stacklevel=2)

    def critical(self, *args) -> None:
        super().critical(" ".join(map(str, args)), stacklevel=2)





logging.setLoggerClass(ColoredLogger)
logger = logging.getLogger("GD Orphans")
