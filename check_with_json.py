import json
import asyncio
import aiohttp
import warnings
import os
from datetime import datetime
from aiohttp import ClientSession, TCPConnector

# 忽略未验证的 HTTPS 请求警告
warnings.filterwarnings("ignore", message="Unverified HTTPS request is being made.*")

# 用户代理字符串，模仿浏览器
user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36"
)

# API Key 和 请求URL的模板
# 判断是否在本地运行，如果是则从环境变量中获取API Key
if os.getenv("LIJIANGAPI_TOKEN") is None:
    print("本地运行，从环境变量中加载并获取API Key")
    from dotenv import load_dotenv

    load_dotenv()
else:
    print("在服务器上运行，从环境变量中获取API Key")

api_key = os.getenv("LIJIANGAPI_TOKEN")
api_url_template = "https://api.76.al/api/web/query?key={}&url={}"

# 代理链接的模板，代理是通过在代理地址后加目标 URL 来请求，代理地址确保以 / 结尾
proxy_url = os.getenv("PROXY_URL")
if proxy_url is not None:
    proxy_url_template = proxy_url.rstrip('/') + "/{}"
else:
    proxy_url_template = None

# 目标JSON数据的URL
json_url = 'https://blog.xxfer.cn/flink.json'

# 信号量用于控制API请求速率，每秒最多5次
api_semaphore = asyncio.Semaphore(5)

# 异步函数，用于获取JSON数据
async def fetch_json_data():
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(json_url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['link_list']
                else:
                    print(f"获取数据失败，状态码: {response.status}")
                    exit()
        except Exception as e:
            print(f"获取JSON数据时发生异常: {e}")
            exit()

# 异步函数，用于检查链接状态
async def check_link(session, item):
    headers = {"User-Agent": user_agent}
    link = item['link']
    latency = -1

    # 1. 首先尝试直接访问
    try:
        start_time = asyncio.get_event_loop().time()
        async with session.get(link, headers=headers, timeout=15, ssl=False) as response:
            latency = asyncio.get_event_loop().time() - start_time
            if response.status < 400:
                print(f"直接访问 {link} 成功，延迟: {latency:.2f} 秒")
                item['latency'] = round(latency, 2)
                item['status'] = 'accessible'
                return item
            else:
                print(f"直接访问 {link} 返回状态 {response.status}")
    except asyncio.TimeoutError:
        print(f"直接访问 {link} 超时")
    except aiohttp.ClientError as e:
        print(f"直接访问 {link} 失败，错误: {e}")
    except Exception as e:
        print(f"直接访问 {link} 时出现未知错误: {e}")

    # 2. 尝试通过代理访问(可选)
    if proxy_url_template:
        proxy_link = proxy_url_template.format(link)
        try:
            start_time = asyncio.get_event_loop().time()
            async with session.get(proxy_link, headers=headers, timeout=15, ssl=False) as response:
                latency = asyncio.get_event_loop().time() - start_time
                if response.status < 400:
                    print(f"通过代理访问 {link} 成功，延迟: {latency:.2f} 秒")
                    item['latency'] = round(latency, 2)
                    item['status'] = 'accessible'
                    return item
                else:
                    print(f"代理访问 {link} 返回状态 {response.status}")
        except asyncio.TimeoutError:
            print(f"代理访问 {link} 超时")
        except aiohttp.ClientError as e:
            print(f"代理访问 {link} 失败，错误: {e}")
        except Exception as e:
            print(f"通过代理访问 {link} 时出现未知错误: {e}")
    else:
        print("未提供代理地址，无法通过代理访问")

    # 3. 如果都失败，尝试通过API访问
    if api_key:
        await asyncio.sleep(0)  # 让出控制权，确保事件循环的顺畅运行
        return await check_link_via_api(session, item)
    else:
        print("API Key 未提供，无法通过API访问")
        item['latency'] = -1
        item['status'] = 'inaccessible'
        return item

# 异步函数，用于通过API检查链接状态
async def check_link_via_api(session, item):
    headers = {"User-Agent": user_agent}
    link = item['link']
    api_url = api_url_template.format(api_key, link)

    async with api_semaphore:  # 控制API请求速率
        try:
            start_time = asyncio.get_event_loop().time()
            async with session.get(api_url, headers=headers, timeout=15, ssl=False) as response:
                response_data = await response.json()
                if response_data.get('code') == 200:
                    latency = response_data.get('exec_time', -1)
                    print(f"通过API访问 {link} 成功，延迟: {latency:.2f} 秒")
                    item['latency'] = round(float(latency), 2)
                    item['status'] = 'accessible'
                else:
                    print(f"API返回错误代码 {response_data.get('code')}，无法访问 {link}")
                    item['latency'] = -1
                    item['status'] = 'inaccessible'
        except asyncio.TimeoutError:
            print(f"API请求 {link} 超时")
            item['latency'] = -1
            item['status'] = 'inaccessible'
        except aiohttp.ClientError as e:
            print(f"API请求 {link} 失败，错误: {e}")
            item['latency'] = -1
            item['status'] = 'inaccessible'
        except Exception as e:
            print(f"通过API访问 {link} 时出现未知错误: {e}")
            item['latency'] = -1
            item['status'] = 'inaccessible'
    return item

# 主函数
async def main():
    link_list = await fetch_json_data()

    # 创建一个TCP连接器，限制最大并发量
    connector = TCPConnector(limit=100)
    async with ClientSession(connector=connector) as session:
        tasks = [check_link(session, item) for item in link_list]
        results = await asyncio.gather(*tasks)

    # 获取当前时间
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 计算可访问和不可访问的数量
    accessible_count = sum(1 for item in results if item.get('status') == 'accessible')
    inaccessible_count = sum(1 for item in results if item.get('status') == 'inaccessible')
    total_count = len(results)

    # 构建结果列表
    link_status = [
        {
            'name': item['name'],
            'link': item['link'],
            'latency': item.get('latency', -1),
            'status': item.get('status', 'unknown')
        }
        for item in results
    ]

    # 将结果写入JSON文件
    output_json_path = './result.json'
    with open(output_json_path, 'w', encoding='utf-8') as file:
        json.dump(
            {
                'timestamp': current_time,
                'accessible_count': accessible_count,
                'inaccessible_count': inaccessible_count,
                'total_count': total_count,
                'link_status': link_status,
            },
            file,
            ensure_ascii=False,
            indent=4,
        )

    print(f"检查完成，结果已保存至 '{output_json_path}' 文件。")

if __name__ == '__main__':
    asyncio.run(main())
