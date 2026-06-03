import datetime
from pathlib import Path

from loguru import logger


SUB_PATH = Path("sub")


@logger.catch
def pre_check() -> str:
    today = datetime.datetime.today()
    path_mon = SUB_PATH / str(today.year) / str(today.month)
    path_mon.mkdir(parents=True, exist_ok=True)

    logger.info("初始化完成")
    return str(path_mon / f"{today.month}-{today.day}.yaml")
