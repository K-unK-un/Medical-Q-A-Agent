'''
地图周边搜索工具:
先获取目标地点的中心经纬度
然后搜索周边（默认医院）
'''
import asyncio
import json
from typing import Annotated

from langchain_core.tools import tool

from utils.env_util import gaode_api_key, gaode_nearby_url, gaode_location_url

import requests


def get_location(
    city: str,
    api_key=gaode_api_key,
    keywords='医院',
    gaode_location_url=gaode_location_url,
    citylimit=True,
):
    '''获取输入城市的经纬度坐标'''
    params = {
        "key": api_key,
        "keywords": keywords,
        "city": city,
        "citylimit": citylimit
    }
    response = requests.get(gaode_location_url, params=params)
    data = json.loads(response.text)
    location = data["pois"][0]["location"]

    return location


@tool
async def nearby_search_tool(
        city: str,
        api_key=gaode_api_key,
        keywords='医院',
        gaode_nearby_url=gaode_nearby_url
):
    """搜索指定城市周边的医院，参数city是城市名称"""
    location = get_location(city)
    params = {
        "key": api_key,
        "location": location,
        "keywords": keywords,
        "offset": 10
    }

    response = requests.get(gaode_nearby_url, params=params)
    data = json.loads(response.text)

    hospital_info = []
    for poi in data['pois']:
        hospital_info.append({
            '名称': poi['name'],
            '地址': poi['address'],
            '标签': poi.get('keytag', ''),
            '营业时间': poi.get('biz_ext', {}).get('opentime2', '未知')
        })

    return hospital_info

# if __name__ == '__main__':
#     result = asyncio.run(nearby_search_tool('北京'))
#     print(result)