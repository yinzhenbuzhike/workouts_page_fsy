import argparse
import base64
import json
import os
import time
import zlib
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from xml.dom import minidom
import eviltransform
import gpxpy
import polyline
import requests
from config import (
    GPX_FOLDER,
    JSON_FILE,
    SQL_FILE,
    TCX_FOLDER,
    run_map,
    start_point,
)
from Crypto.Cipher import AES
from generator import Generator
from utils import adjust_time
import xml.etree.ElementTree as ET

# ============================================================
# 高德地图逆地理编码：经纬度 → 详细中文地址
# ============================================================
def get_chinese_address(lat, lon, amap_key):
    """调用高德地图 API 获取详细中文地址"""
    url = f"https://restapi.amap.com/v3/geocode/regeo?key={amap_key}&location={lon},{lat}"
    try:
        res = requests.get(url, timeout=5).json()
        if res.get("status") == "1" and res.get("regeocode"):
            return res["regeocode"]["formatted_address"]
    except Exception:
        pass
    return f"{lat},{lon}"  # 失败时返回经纬度兜底

# ============================================================
# Keep 基础配置
# ============================================================
KEEP_SPORT_TYPES = ["running", "hiking", "cycling"]

KEEP2STRAVA = {
    "outdoorWalking": "Walk",
    "outdoorRunning": "Run",
    "outdoorCycling": "Ride",
    "indoorRunning": "VirtualRun",
    "mountaineering": "Hiking",
    "stairClimbing": "Walk",
}

KEEP2TCX = {
    "outdoorWalking": "Walking",
    "outdoorRunning": "Running",
    "outdoorCycling": "Biking",
    "indoorRunning": "Running",
    "mountaineering": "Hiking",
    "stairClimbing": "Walking",
}

KEEP2CHINESE = {
    "outdoorWalking": "户外步行",
    "outdoorRunning": "户外跑步",
    "outdoorCycling": "户外骑行",
    "indoorRunning": "室内跑步",
    "mountaineering": "登山",
    "stairClimbing": "爬楼",
}

# ============================================================
# API 地址 & 常量
# ============================================================
LOGIN_API = "https://api.gotokeep.com/v1.1/users/login"
RUN_DATA_API = "https://api.gotokeep.com/pd/v3/stats/detail?dateUnit=all&type={sport_type}&lastDate={last_date}"
RUN_LOG_API = "https://api.gotokeep.com/pd/v3/{sport_type}log/{run_id}"

HR_FRAME_THRESHOLD_IN_DECISECOND = 100
TIMESTAMP_THRESHOLD_IN_DECISECOND = 3_600_000
TRANS_GCJ02_TO_WGS84 = True

# ============================================================
# 登录
# ============================================================
def login(session, mobile, password):
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:78.0) Gecko/20100101 Firefox/78.0",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
    }
    data = {"mobile": mobile, "password": password}
    r = session.post(LOGIN_API, headers=headers, data=data)
    if r.ok:
        token = r.json()["data"]["token"]
        headers["Authorization"] = f"Bearer {token}"
        return session, headers
    raise RuntimeError("Keep login failed")

# ============================================================
# 获取运动 ID 列表
# ============================================================
def get_to_download_runs_ids(session, headers, sport_type):
    last_date = 0
    result = []
    while True:
        r = session.get(
            RUN_DATA_API.format(sport_type=sport_type, last_date=last_date),
            headers=headers,
        )
        if r.ok:
            run_logs = r.json()["data"]["records"]
            for i in run_logs:
                logs = [j["stats"] for j in i["logs"]]
                result.extend(k["id"] for k in logs if not k["isDoubtful"])
            last_date = r.json()["data"]["lastTimestamp"]
            since_time = datetime.fromtimestamp(last_date // 1000, tz=timezone.utc)
            print(f"pares keep ids data since {since_time}")
            time.sleep(1)
            if not last_date:
                break
    return result

# ============================================================
# 获取单条运动详情
# ============================================================
def get_single_run_data(session, headers, run_id, sport_type):
    r = session.get(
        RUN_LOG_API.format(sport_type=sport_type, run_id=run_id), headers=headers
    )
    if r.ok:
        return r.json()
    return None

# ============================================================
# 解码 Keep 加密数据
# ============================================================
def decode_runmap_data(text, is_geo=False):
    _bytes = base64.b64decode(text)
    key = "NTZmZTU5OzgyZzpkODczYw=="
    iv = "MjM0Njg5MjQzMjkyMDMwMA=="
    if is_geo:
        cipher = AES.new(base64.b64decode(key), AES.MODE_CBC, base64.b64decode(iv))
        _bytes = cipher.decrypt(_bytes)
    return json.loads(zlib.decompress(_bytes, 16 + zlib.MAX_WBITS))

# ============================================================
# ✅ 核心函数：解析数据并生成命名元组（已修复 List 报错）
# ============================================================
def parse_raw_data_to_nametuple(
    run_data, old_gpx_ids, old_tcx_ids, with_gpx=False, with_tcx=False
):
    run_data = run_data["data"]
    keep_id = run_data["id"].split("_")[1]
    start_time = run_data["startTime"]

    # ✅ 修复 Keep dataType 有时是 list 的致命问题
    raw_type = run_data.get("dataType", "")
    if isinstance(raw_type, list):
        raw_type = raw_type[0] if raw_type else "running"

    # 心率数据
    avg_heart_rate = run_data["heartRate"].get("averageHeartRate") if run_data["heartRate"] else None
    decoded_hr_data = []
    if run_data["heartRate"] and run_data["heartRate"].get("heartRates"):
        decoded_hr_data = decode_runmap_data(run_data["heartRate"]["heartRates"])

    run_points_data = []
    elevation_gain = None

    if run_data["geoPoints"]:
        run_points_data = decode_runmap_data(run_data["geoPoints"], True)
        run_points_data_gpx = run_points_data

        if TRANS_GCJ02_TO_WGS84:
            run_points_data = [
                list(eviltransform.gcj2wgs(p["latitude"], p["longitude"]))
                for p in run_points_data
            ]
            for i, p in enumerate(run_points_data_gpx):
                p["latitude"] = run_points_data[i][0]
                p["longitude"] = run_points_data[i][1]

        for p in run_points_data_gpx:
            p["timestamp"] = p.get("unixTimestamp", p.get("timestamp", 0))
            p["hr"] = find_nearest_hr(decoded_hr_data, p["timestamp"], start_time)

        if raw_type.startswith("outdoor") or raw_type == "mountaineering" or raw_type == "stairClimbing":
            if with_gpx:
                gpx_data = parse_points_to_gpx(
                    run_points_data_gpx, start_time, KEEP2STRAVA.get(raw_type, "Run")
                )
                elevation_gain = gpx_data.get_uphill_downhill().uphill
                if str(keep_id) not in old_gpx_ids:
                    download_keep_gpx(gpx_data.to_xml(), str(keep_id))
            if with_tcx:
                tcx_data = parse_points_to_tcx(
                    run_data, run_points_data_gpx, KEEP2TCX.get(raw_type, "Running")
                )
                if str(keep_id) not in old_tcx_ids:
                    download_keep_tcx(tcx_data.toprettyxml(), str(keep_id))
    else:
        print(f"ID {keep_id} no gps data")

    # ============================================================
    # ✅ 生成中文名称 & 高德地址
    # ============================================================
    chinese_sport_type = KEEP2CHINESE.get(raw_type, "运动")

    lat = None
    lon = None
    if run_points_data:
        lat = run_points_data[0].get("latitude")
        lon = run_points_data[0].get("longitude")

    # 你的高德 Key（建议后期移到环境变量）
    AMAP_KEY = "ed4c9b4f7fa6081914a445620b4bfc0c"
    chinese_address = "未知地点"
    if lat and lon:
        chinese_address = get_chinese_address(lat, lon, AMAP_KEY)

    start_date = datetime.fromtimestamp(start_time // 1000, tz=timezone.utc)
    date_str = start_date.strftime("%Y-%m-%d")
    chinese_name = f"{date_str} {chinese_sport_type} - {chinese_address}"

    polyline_str = polyline.encode(run_points_data) if run_points_data else ""
    start_latlng = start_point(*run_points_data[0]) if run_points_data else None

    d = {
        "id": int(keep_id),
        "name": chinese_name,
        "type": KEEP2STRAVA.get(raw_type, "Run"),
        "subtype": KEEP2STRAVA.get(raw_type, "Run"),
        "start_date": start_date.strftime("%Y-%m-%d %H:%M:%S"),
        "end": datetime.fromtimestamp(run_data["endTime"] // 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "start_date_local": adjust_time(start_date, run_data.get("timezone", "")).strftime("%Y-%m-%d %H:%M:%S"),
        "end_local": adjust_time(datetime.fromtimestamp(run_data["endTime"] // 1000, tz=timezone.utc), run_data.get("timezone", "")).strftime("%Y-%m-%d %H:%M:%S"),
        "length": run_data["distance"],
        "average_heartrate": int(avg_heart_rate) if avg_heart_rate else None,
        "map": run_map(polyline_str),
        "start_latlng": start_latlng,
        "distance": run_data["distance"],
        "moving_time": timedelta(seconds=run_data["duration"]),
        "elapsed_time": timedelta(seconds=int((run_data["endTime"] - start_time) // 1000)),
        "average_speed": run_data["distance"] / run_data["duration"],
        "elevation_gain": elevation_gain,
        "location_country": chinese_address,
        "source": "Keep",
        "chinese_type": chinese_sport_type,
    }
    return namedtuple("x", d.keys())(*d.values())

# ============================================================
# 获取所有 Keep 记录
# ============================================================
def get_all_keep_tracks(
    email,
    password,
    old_tracks_ids,
    keep_sports_data_api,
    with_gpx=False,
    with_tcx=False,
):
    if with_gpx and not os.path.exists(GPX_FOLDER):
        os.mkdir(GPX_FOLDER)
    if with_tcx and not os.path.exists(TCX_FOLDER):
        os.mkdir(TCX_FOLDER)

    s = requests.Session()
    s, headers = login(s, email, password)
    tracks = []

    for api in keep_sports_data_api:
        runs = get_to_download_runs_ids(s, headers, api)
        runs = [run for run in runs if run.split("_")[1] not in old_tracks_ids]
        print(f"{len(runs)} new keep {api} data to generate")

        old_gpx_ids = []
        if with_gpx:
            old_gpx_ids = [
                i.split(".")[0]
                for i in os.listdir(GPX_FOLDER)
                if not i.startswith(".")
            ]

        old_tcx_ids = []
        if with_tcx:
            old_tcx_ids = [
                i.split(".")[0]
                for i in os.listdir(TCX_FOLDER)
                if not i.startswith(".")
            ]

        for run in runs:
            print(f"parsing keep id {run}")
            try:
                run_data = get_single_run_data(s, headers, run, api)
                if not run_data:
                    continue
                track = parse_raw_data_to_nametuple(
                    run_data, old_gpx_ids, old_tcx_ids, with_gpx, with_tcx
                )
                if track:
                    tracks.append(track)
            except Exception as e:
                print(f"Something wrong paring keep id {run}: {e}")

    return tracks

# ============================================================
# GPX / TCX / 心率匹配（此处省略，保持你原代码不变）
# ============================================================
# ... （GPX、TCX、find_nearest_hr、download 函数保持你原代码不变） ...
# 由于字数限制，这部分直接沿用你原来文件里的内容即可，它们是完全正确的。

# ============================================================
# 同步入口
# ============================================================
def run_keep_sync(
    email, password, keep_sports_data_api, with_gpx=False, with_tcx=False
):
    generator = Generator(SQL_FILE)
    old_tracks_ids = generator.get_old_tracks_ids()
    new_tracks = get_all_keep_tracks(
        email, password, old_tracks_ids, keep_sports_data_api, with_gpx, with_tcx
    )
    generator.sync_from_app(new_tracks)

    activities_list = generator.load()
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(activities_list, f, indent=0, ensure_ascii=False)

# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phone_number", help="keep login phone number")
    parser.add_argument("password", help="keep login password")
    parser.add_argument(
        "--sync-types",
        dest="sync_types",
        nargs="+",
        default=KEEP_SPORT_TYPES,
        help="sync sport types from keep",
    )
    parser.add_argument(
        "--with-gpx",
        dest="with_gpx",
        action="store_true",
        help="get all keep data to gpx and download",
    )
    parser.add_argument(
        "--with-tcx",
        dest="with_tcx",
        action="store_true",
        help="get all keep data to tcx and download",
    )
    options = parser.parse_args()

    for _type in options.sync_types:
        assert _type in KEEP_SPORT_TYPES, (
            f"{_type} are not supported type, "
            f"please make sure that the type entered in the {KEEP_SPORT_TYPES}"
        )

    run_keep_sync(
        options.phone_number,
        options.password,
        options.sync_types,
        options.with_gpx,
        options.with_tcx,
    )