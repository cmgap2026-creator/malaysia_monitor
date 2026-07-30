#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
马来西亚 4 部门官网实时监测脚本 (双端兼容·虚拟显示器·新标签页防爬版)
- 环境智适应：Windows本地弹真实窗口；Linux云端(GitHub)自动拉起 Xvfb 虚拟显示器。
- 抓取逻辑：关闭无头模式，主页不动，通过新标签页抓取文章，阅后即焚，高度仿人。
- 运行模式：单次运行 (专为 GitHub Actions Cron 定时器设计)。
"""

import psutil
import os
import json
import time
import random
import hashlib
import platform
import re
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

# ==================== 时区定义 ====================
MYT_TZ = timezone(timedelta(hours=8))

# ==================== 配置区 ====================
MALAYSIA_TARGETS = [
    {"id": "MOT", "name": "马来西亚交通部 (MOT)", "url": "https://www.mot.gov.my"},
    {"id": "PMO", "name": "马来西亚总理府 (PMO)", "url": "https://www.pmo.gov.my"},
    {"id": "KLN", "name": "马来西亚外交部 (KLN)", "url": "https://www.kln.gov.my"},
    {"id": "MOTAC", "name": "马来西亚旅游艺术文化部(MOTAC)", "url": "https://www.motac.gov.my"}
]
ITEMS_FILE = "items_malaysia.json"
HTML_FILE = "index.html"
BETWEEN_ARTICLES_DELAY = (1, 2)

# ==================== 浏览器驱动 ====================
def create_driver():
    chrome_options = Options()
    chrome_options.page_load_strategy = 'eager'
    
    chrome_options.add_argument('--blink-settings=imagesEnabled=false')
    chrome_options.add_argument('--mute-audio')
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.media_stream": 2,
        "profile.default_content_setting_values.plugins": 2,
        "profile.default_content_setting_values.notifications": 2
    }
    chrome_options.add_experimental_option("prefs", prefs)

    # 🚨 保持关闭无头模式，依靠环境智适应来处理显示器问题
    # chrome_options.add_argument("--headless=new") 
    
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1280,720")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]
    chrome_options.add_argument(f"--user-agent={random.choice(user_agents)}")
    driver = webdriver.Chrome(options=chrome_options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
    })
    return driver

# ==================== 翻译函数 ====================
def translate_text(text, target_lang='zh-CN'):
    if not text or len(text.strip()) == 0: return text
    try: return GoogleTranslator(source='auto', target=target_lang).translate(text)
    except: return text

# ==================== 核心抓取逻辑 ====================
def extract_links_from_site(driver, site_url):
    for attempt in range(2):
        try:
            driver.set_page_load_timeout(60)
            driver.get(site_url)
            break
        except Exception as e:
            err_msg = str(e).split('\n')[0]
            print(f"    ⚠️ 主页加载受阻 (尝试 {attempt+1}/2): {err_msg}")
            time.sleep(2)
    else: return []

    try:
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(2)
    except: return []
    
    soup = BeautifulSoup(driver.page_source, "html.parser")
    domain = site_url.split("/")[2]
    article_links = set()
    news_keywords = ['news', 'media', 'press', 'kenyataan', 'berita', 'article', 'announcement', 'publication', 'speech']
    
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if not href or href.startswith('#') or href.startswith('javascript:'): continue
        if href.startswith('/'): full_url = f"https://{domain}" + href
        elif href.startswith('http'): full_url = href
        else: continue
            
        if domain in full_url:
            href_lower = href.lower()
            if any(kw in href_lower for kw in news_keywords):
                if not any(ext in href_lower for ext in ['.pdf', '.jpg', '.png', '.zip', '.doc']):
                    article_links.add(full_url)
    return list(article_links)[:25]

def fetch_article_details_in_new_tab(driver, article_url, site_name):
    main_window = driver.current_window_handle
    try:
        driver.execute_script(f"window.open('{article_url}', '_blank');")
        WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))
        for window_handle in driver.window_handles:
            if window_handle != main_window:
                driver.switch_to.window(window_handle)
                break
        
        driver.set_page_load_timeout(30)
        time.sleep(1.5)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        
        title = None
        h1 = soup.find('h1')
        if h1: title = h1.get_text(strip=True)
        if not title:
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'): title = og_title['content'].strip()
        if not title:
            title_tag = soup.find('title')
            if title_tag: title = title_tag.get_text(strip=True)
        if not title or len(title) < 5: return None
        translated_title = translate_text(title)
        
        pub_time = None
        for meta_name in ['article:published_time', 'date', 'pubdate', 'parsely-pub-date']:
            meta_date = soup.find('meta', {'property': meta_name}) or soup.find('meta', {'name': meta_name})
            if meta_date and meta_date.get('content'):
                try:
                    pub_time = datetime.fromisoformat(meta_date['content'].replace('Z', '+00:00'))
                    break
                except: pass
        if not pub_time:
            text_content = soup.get_text()
            date_match = re.search(r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})|(\d{4}-\d{2}-\d{2})', text_content, re.IGNORECASE)
            if date_match:
                date_str = date_match.group(0)
                try:
                    if '-' in date_str: pub_time = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    else: pub_time = datetime.strptime(date_str, "%d %B %Y").replace(tzinfo=timezone.utc)
                except: pass
        if not pub_time: pub_time = datetime.now(timezone.utc)
            
        content_paragraphs = []
        for p in soup.find_all('p'):
            p_text = p.get_text(strip=True)
            if len(p_text) > 30 and not any(kw in p_text.lower() for kw in ['copyright', 'rights reserved']):
                content_paragraphs.append(p_text)
                if len(content_paragraphs) >= 3: break
        summary = " ".join(content_paragraphs) if content_paragraphs else "暂无正文摘要"
        if len(summary) > 400: summary = summary[:397] + "..."
        translated_summary = translate_text(summary)
        
        url_hash = hashlib.md5(article_url.encode()).hexdigest()[:12]
        return {
            "id": f"my_{url_hash}", "type": "article", "source": site_name,
            "title": title, "translated_title": translated_title,
            "summary": summary, "translated_summary": translated_summary,
            "url": article_url, "timestamp": datetime.now(timezone.utc).isoformat(),
            "original_time": pub_time.isoformat()
        }
    except: return None
    finally:
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(main_window)

# ==================== 单线程部门处理 ====================
def site_worker_task(site_info, cutoff_utc, existing_ids):
    thread_id = site_info["id"]
    site_name = site_info["name"]
    site_url = site_info["url"]
    
    thread_start_time = time.time()
    print(f"\n🚀 [线程-{thread_id}] 开始处理: {site_name}")
    
    driver = create_driver()
    collected_items = []
    
    try:
        links = extract_links_from_site(driver, site_url)
        for idx, link in enumerate(links, 1):
            article = fetch_article_details_in_new_tab(driver, link, site_name)
            if not article: continue
            try:
                pub_time = datetime.fromisoformat(article["original_time"].replace("Z", "+00:00"))
                if pub_time < cutoff_utc: continue
            except: pass
            if article["id"] not in existing_ids:
                collected_items.append(article)
                existing_ids.add(article["id"])
                print(f"  ✅ [线程-{thread_id}] [{idx}/{len(links)}] 新增: {article['title'][:40]}...")
            time.sleep(random.uniform(*BETWEEN_ARTICLES_DELAY))
    except Exception as e:
        err_msg = str(e).split('\n')[0]
        print(f"  ⏭️ [线程-{thread_id}] 抓取受阻跳过 ({err_msg})")
    finally:
        try: driver.quit()
        except: pass
        cost_time = time.time() - thread_start_time
        print(f"✅ [线程-{thread_id}] {site_name} 结束，耗时: {cost_time:.2f} 秒")
        
    return {"items": collected_items, "site_name": site_name, "site_url": site_url, "cost_time": cost_time}

# ==================== 数据与HTML ====================
def load_items():
    if os.path.exists(ITEMS_FILE):
        with open(ITEMS_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return []

def save_items(items):
    with open(ITEMS_FILE, "w", encoding="utf-8") as f: json.dump(items, f, indent=2, ensure_ascii=False)

def clean_old_data(hours=24):
    if not os.path.exists(ITEMS_FILE): return
    with open(ITEMS_FILE, "r", encoding="utf-8") as f: all_items = json.load(f)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    kept_items = [i for i in all_items if datetime.fromisoformat(i.get("original_time", i.get("timestamp")).replace("Z", "+00:00")) >= cutoff]
    save_items(kept_items)

def generate_html(recent_items, site_stats, total_cost_time, total_traffic_mb):
    articles = sorted(recent_items, key=lambda x: x.get("original_time", ""), reverse=True)
    update_time = datetime.now(MYT_TZ).strftime("%Y-%m-%d %H:%M:%S MYT")
    
    speed_dashboard_html = "<div class='dashboard-container'>"
    for stat in site_stats:
        speed_dashboard_html += f"""
        <div class="speed-card">
            <div class="site-name">{stat['site_name']}</div>
            <div class="site-url"><a href="{stat['site_url']}" target="_blank">{stat['site_url']}</a></div>
            <div class="time-cost">⏱️ 耗时: <span>{stat['cost_time']:.2f} 秒</span></div>
        </div>
        """
    speed_dashboard_html += "</div>"
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>马来西亚政府 4 部门实时资讯监测</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 1000px; margin: auto; padding: 20px; background: #f4f6f9; }}
        h1 {{ color: #1a365d; border-left: 5px solid #3182ce; padding-left: 15px; }}
        .status-panel {{ background: #ebf8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #bee3f8; }}
        .status-panel p {{ margin: 5px 0; color: #2b6cb0; font-size: 0.95em; }}
        .traffic-badge {{ display: inline-block; background: #2b6cb0; color: white; padding: 4px 10px; border-radius: 4px; font-weight: bold; margin-top: 5px; }}
        h2 {{ color: #2d3748; font-size: 1.4em; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 30px; }}
        .dashboard-container {{ display: flex; flex-wrap: wrap; gap: 15px; margin-bottom: 25px; }}
        .speed-card {{ background: white; padding: 15px; border-radius: 8px; border-left: 4px solid #38b2ac; box-shadow: 0 1px 3px rgba(0,0,0,0.1); flex: 1; min-width: 200px; }}
        .speed-card .site-name {{ font-weight: bold; color: #2c5282; font-size: 0.95em; }}
        .speed-card .site-url a {{ font-size: 0.8em; color: #718096; text-decoration: none; word-break: break-all; }}
        .speed-card .time-cost {{ margin-top: 10px; font-size: 0.9em; color: #4a5568; }}
        .speed-card .time-cost span {{ font-weight: bold; color: #e53e3e; }}
        .card {{ background: white; padding: 18px; margin-bottom: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border-left: 4px solid #3182ce; }}
        .source {{ font-weight: bold; color: #2b6cb0; font-size: 0.9em; margin-bottom: 5px; }}
        .title {{ font-size: 1.1em; margin: 5px 0; font-weight: 600; }}
        .translated-title {{ background: #f0f7ff; padding: 8px; border-radius: 5px; color: #1a202c; font-weight: 500; margin: 8px 0; }}
        .summary {{ font-size: 0.9em; color: #4a5568; margin-top: 8px; line-height: 1.5; }}
        .translated-summary {{ font-size: 0.9em; color: #2d3748; background: #faf5ff; padding: 8px; border-radius: 5px; margin-top: 5px; line-height: 1.5; }}
        .time {{ font-size: 0.8em; color: #a0aec0; margin-top: 10px; }}
        a {{ color: #3182ce; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <h1>📡 马来西亚政府 4 部门实时监测面板</h1>
    <div class="status-panel">
        <p>🕒 <strong>当前数据刷新时间：</strong>{update_time}</p>
        <p>📊 <strong>监测范围：</strong>最近 24 小时发布信息</p>
        <div class="traffic-badge">
            🚀 本轮系统总耗时: {total_cost_time:.2f} 秒 &nbsp;&nbsp;|&nbsp;&nbsp; ⛽ 本轮消耗流量: {total_traffic_mb:.2f} MB
        </div>
    </div>
    <h2>⚡ 各部门官网实时响应速度</h2>
    {speed_dashboard_html}
    <h2>📰 部门最新动态 ({len(articles)})</h2>
    """
    if not articles: html += "<p>最近 24 小时内暂无新增动态。</p>"
    else:
        for a in articles:
            html += f"""
            <div class="card">
                <div class="source">🏛️ {a.get('source')}</div>
                <div class="title">📝 原文标题: <a href="{a.get('url')}" target="_blank">{a.get('title')}</a></div>
                <div class="translated-title">🇨🇳 译文标题: {a.get('translated_title')}</div>
                <div class="summary">📄 原文摘要: {a.get('summary')}</div>
                <div class="translated-summary">🇨🇳 译文摘要: {a.get('translated_summary')}</div>
                <div class="time">🕒 发布时间: {a.get('original_time')}</div>
            </div>
            """
    html += "</body></html>"
    with open(HTML_FILE, "w", encoding="utf-8") as f: f.write(html)

# ==================== 主入口 ====================
def main():
    print(f"\n==========================================")
    print(f"[{datetime.now(MYT_TZ).strftime('%Y-%m-%d %H:%M:%S MYT')}] 启动 4 部门官网实时抓取...")
    
    # 🟢 智能环境侦测：根据操作系统决定是否开启虚拟显示器
    is_linux = platform.system() == 'Linux'
    vdisplay = None
    if is_linux:
        try:
            from pyvirtualdisplay import Display
            vdisplay = Display(visible=0, size=(1280, 720))
            vdisplay.start()
            print("☁️ 检测到 Linux 云端环境，已在后台拉起 Xvfb 虚拟显示器！")
        except Exception as e:
            print(f"⚠️ 虚拟显示器拉起失败，程序可能即将崩溃: {e}")
    else:
        print("💻 检测到 Windows/Mac 本地环境，准备弹出物理浏览器窗口！")

    start_bytes = psutil.net_io_counters().bytes_recv
    start_time = time.time()
    
    clean_old_data(hours=24)
    all_items = load_items()
    existing_ids = {item["id"] for item in all_items}
    
    cutoff_utc = datetime.now(timezone.utc) - timedelta(hours=24)
    new_collected_items = []
    site_stats = []
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(site_worker_task, target, cutoff_utc, existing_ids) for target in MALAYSIA_TARGETS]
        for future in futures:
            try:
                result = future.result()
                new_collected_items.extend(result["items"])
                site_stats.append({"site_name": result["site_name"], "site_url": result["site_url"], "cost_time": result["cost_time"]})
            except Exception as e:
                print(f"❌ 线程发生异常: {str(e).split(chr(10))[0]}")
                
    if new_collected_items:
        all_items.extend(new_collected_items)
        save_items(all_items)
        print(f"\n✅ 成功新增 {len(new_collected_items)} 条内容！")
    else:
        print("\n📭 无新增内容。")
        
    end_time = time.time()
    end_bytes = psutil.net_io_counters().bytes_recv
    
    total_cost_time = end_time - start_time
    total_traffic_mb = (end_bytes - start_bytes) / (1024 * 1024)
        
    generate_html(all_items, site_stats, total_cost_time, total_traffic_mb)
    print(f"🎉 网页 {HTML_FILE} 生成成功！")
    
    # 🟢 抓取结束，优雅关闭云端虚拟显示器
    if vdisplay:
        vdisplay.stop()
    
    return total_cost_time, total_traffic_mb

if __name__ == "__main__":
    # 🚨 把死循环加回来！让云端服务器像你本地电脑一样一直挂机跑！
    while True:
        total_time, total_traffic = main()
        print(f"\n📊 运行报告：")
        print(f"⏱️ 并发总耗时: {total_time:.2f} 秒")
        print(f"⛽ 消耗总流量: {total_traffic:.2f} MB")
        print("\n⏳ 5 分钟倒计时开始，休息完毕后将自动进入下一轮监测...")
        time.sleep(300) # 强行等待 5 分钟")
