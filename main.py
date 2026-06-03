import base64
import binascii
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import requests
import yaml
from loguru import logger
from retry import retry
from tqdm import tqdm

from pre_check import pre_check


SUB_KEYS = ("机场订阅", "clash订阅", "v2订阅")
SUB_URL_PATTERN = re.compile(r"https?://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]")
NODE_SCHEMES = ("ss://", "ssr://", "vmess://", "trojan://")
REQUEST_TIMEOUT = 10
MAX_WORKERS = 32


def empty_subscriptions() -> Dict[str, List[str]]:
    return {key: [] for key in SUB_KEYS}


@logger.catch
def yaml_check(path_yaml: str) -> Dict[str, List[str]]:
    path = Path(path_yaml)
    if not path.is_file():
        logger.info("当天订阅文件不存在，将创建新文件")
        return empty_subscriptions()

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    result = empty_subscriptions()
    for key in SUB_KEYS:
        value = data.get(key, [])
        result[key] = value if isinstance(value, list) else []

    logger.info("读取订阅文件成功")
    return result


@logger.catch
def get_config(config_path: str = "config.yaml") -> List[str]:
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    channels = data.get("tgchannel", [])
    if not isinstance(channels, list):
        logger.warning("config.yaml 中 tgchannel 不是列表，已忽略")
        return []

    result = []
    for channel in channels:
        if not isinstance(channel, str) or not channel.strip():
            continue
        name = channel.rstrip("/").split("/")[-1]
        if name:
            result.append(f"https://t.me/s/{name}")

    return sorted(set(result))


def get_channel_http(channel_url: str) -> List[str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(channel_url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("{} 获取失败: {}", channel_url, exc)
        return []

    logger.info("{} 获取成功", channel_url)
    return SUB_URL_PATTERN.findall(resp.text)


def filter_base64(text: str) -> bool:
    return any(scheme in text for scheme in NODE_SCHEMES)


def looks_like_v2_subscription(text: str) -> bool:
    sample = re.sub(r"\s+", "", text[:512])
    sample += "=" * (-len(sample) % 4)
    try:
        decoded = base64.b64decode(sample, validate=False).decode("utf-8", errors="ignore")
    except (binascii.Error, ValueError, TypeError):
        return False
    return filter_base64(decoded)


@retry(tries=2, delay=1)
def fetch_subscription(url: str) -> requests.Response:
    headers = {"User-Agent": "ClashforWindows/0.18.1"}
    response = requests.get(url, headers=headers, timeout=5)
    response.raise_for_status()
    return response


def sub_check(url: str) -> Optional[str]:
    try:
        response = fetch_subscription(url)
    except requests.RequestException:
        return None

    if response.headers.get("subscription-userinfo"):
        return "机场订阅"
    if "proxies:" in response.text:
        return "clash订阅"
    if looks_like_v2_subscription(response.text):
        return "v2订阅"
    return None


def collect_channel_urls(channels: Iterable[str]) -> List[str]:
    urls: Set[str] = set()
    for channel_url in channels:
        urls.update(get_channel_http(channel_url))
    return sorted(urls)


def classify_urls(urls: Iterable[str]) -> Dict[str, Set[str]]:
    result = {key: set() for key in SUB_KEYS}
    urls = list(urls)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {executor.submit(sub_check, url): url for url in urls}
        with tqdm(total=len(future_to_url), desc="订阅筛选：") as bar:
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    sub_type = future.result()
                except Exception as exc:
                    logger.warning("{} 筛选失败: {}", url, exc)
                    sub_type = None
                if sub_type:
                    result[sub_type].add(url)
                bar.update(1)
    return result


def merge_subscriptions(
    old_subscriptions: Dict[str, List[str]], new_subscriptions: Dict[str, Set[str]]
) -> Dict[str, List[str]]:
    merged = empty_subscriptions()
    for key in SUB_KEYS:
        merged[key] = sorted(set(old_subscriptions.get(key, [])) | new_subscriptions.get(key, set()))
    return merged


def main() -> None:
    path_yaml = pre_check()
    old_subscriptions = yaml_check(path_yaml)
    channels = get_config()
    logger.info("读取 config 成功，共 {} 个频道", len(channels))

    url_list = collect_channel_urls(channels)
    logger.info("开始筛选，共 {} 个候选链接", len(url_list))

    new_subscriptions = classify_urls(url_list)
    merged = merge_subscriptions(old_subscriptions, new_subscriptions)

    with open(path_yaml, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)

    logger.info(
        "筛选完成：机场订阅 {} 个，clash 订阅 {} 个，v2 订阅 {} 个",
        len(merged["机场订阅"]),
        len(merged["clash订阅"]),
        len(merged["v2订阅"]),
    )


if __name__ == "__main__":
    main()
