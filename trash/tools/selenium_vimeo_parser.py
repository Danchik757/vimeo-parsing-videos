import seleniumwire.undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from telegram.ext import Updater

import socket
import time

import json
def LoadJsonFromFile(filename):
	f = open(filename, "r")
	obj = json.load(f, strict=False)
	f.close()
	return obj
def DumpJsonToFile(obj, filename):
	f = open(filename, "w")
	json.dump(obj, f, indent = 4, sort_keys = True)
	f.close()
     
TELEGRAM_TOKEN =         '###'
TELEGRAM_CHAT_ID =         -1
HOST_NAME = socket.gethostname().replace('-', '_')


name = "VimeoSpider"
tags = ["hd", "4k", "uhd", "raw"]
key_words = ["the", "of", "in", "be", "to", "and", "that", "have", "I", "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",\
    "this", "but", "his", "by", "from", "they", "we", "say", "her", "she", "or", "an", "will", "my", "one", "all", "would", "there", "their", "what", \
    "so", "up", "out", "if", "about", "who", "get", "which", "go", "me", "when", "make", "can", "like", "time", "no", "just", "him", "know", "take", \
    "people", "into", "year", "your", "good", "some", "could", "them", "see", "other", "than", "then", "now", "look", "only", "come", "its", "over", "think", "also", \
    "back", "after", "use", "two", "how", "our", "work", "first", "well", "way", "even", "new", "want", "because", "any", "these", 'give', "day", "most", "us"]
licenses = ["by", "cc0"]
base_url_search_wo_licenses = "https://vimeo.com/search?sort=latest_desc&q={key_words}"
base_url_search = "https://vimeo.com/search?sort=latest_desc&license={license}&q={key_words}"
base_url_tags = "https://vimeo.com/tag:{tag}/sort:date/format:thumbnail"
video_url = "https://vimeo.com/{id}"
start_page = 1
checked_ids = None
checked_ids_path = "list_of_checked.json"

start_urls = [base_url_search.format(key_words = k, license = l) + '&page={page}' for k in key_words for l in licenses] + \
                    [base_url_search_wo_licenses.format(key_words = k) + '&page={page}' for k in key_words]
                    #[base_url_tags.format(tag = t) + '/page:{page}' for t in tags]
#start_urls = [base_url_tags.format(page = start_page, tag = t) for t in tags]
# checked_ids = LoadJsonFromFile(checked_ids_path)
updater = Updater(TELEGRAM_TOKEN)

def create_driver(proxy_address):
    proxy_options = {
        "proxy": {
            "http": 'http://'+proxy_address,
            "https": 'https://'+proxy_address,
        }
    }


    chrome_options = uc.ChromeOptions()
    chrome_options.headless = False
    chrome_options.add_argument('--ignore-certificate-errors')  # Игнорировать ошибки сертификатов
    chrome_options.add_argument('--allow-insecure-localhost')  # Разрешить самоподписанные сертификаты на локальном хосте
    # Отключаем загрузку изображений
    prefs = {
        "profile.managed_default_content_settings.images": 2  # 2 - отключение изображений
    }
    chrome_options.add_experimental_option("prefs", prefs)
    #chrome_options.add_argument(f'--proxy-server={"http://"+proxy_address}')
    driver = uc.Chrome(seleniumwire_options=proxy_options, options=chrome_options, use_subprocess=True, headless=False)

    return driver

def get_url(driver, url):
    while True:
        try:
            if driver is None:
                driver = create_driver(f"Cf0rm2:y4XUAq@45.11.125.162:9824")
                
            driver.get(url)
            return driver
        except:
            try:
                driver.quit()
            except:
                 pass
            driver = None

# Опции для Chrome
#driver = uc.Chrome(options=chrome_options, use_subprocess=True, headless=False)
#driver.page_load_strategy = 'none'
#driver.set_page_load_timeout(600)
#driver.set_script_timeout(30)  # Установите таймаут для выполнения скриптов

from tqdm import tqdm
import os

bad_urls_file = 'bad_urls.txt'
bad_urls = set()

if os.path.exists(bad_urls_file):
    bad_urls = set(json.load(open(bad_urls_file, 'r', encoding='utf-8')))

parsed_data = []
result_file = "parsed_data.json"
if os.path.exists(result_file):
     parsed_data = json.load(open(result_file, 'r', encoding='utf-8'))

parsed_urls = []
parsed_urls_file = "parsed_urls.json"

if os.path.exists(parsed_urls_file):
     parsed_urls = json.load(open(parsed_urls_file, 'r', encoding='utf-8'))

updater.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text='Start parsing Vimeo\n\n#' + HOST_NAME + " #parsing", parse_mode='HTML')

driver = None
for url in tqdm(start_urls):
    # elem = driver.find_element(By.CSS_SELECTOR, "input[type='search']")
    # elem.click()
    # elem.send_keys(Keys.CONTROL + "a")
    # elem.send_keys(Keys.DELETE)
    # elem.send_keys(url)
    # elem.send_keys(Keys.ENTER)
    # input()
    page = start_page
    while True:
        cur_url = url.format(page = page)
        if cur_url in parsed_urls:
             page += 1
             continue
        
        driver = get_url(driver, cur_url)
#        driver.execute_script("setTimeout(() => window.stop(), 15000);")  # Остановите загрузку через 10 секунд
#        time.sleep(10)  # Дайте время загрузиться
        time.sleep(1)
        need_break = False
        try:
            elems = driver.find_elements(By.CSS_SELECTOR, "a[class='chakra-card css-4n73h9']")
            
            bad_count = 0
            while True:
                start_time = time.time()
                while len(elems) == 0 and time.time() - start_time < 10:
                    elems = driver.find_elements(By.CSS_SELECTOR, "a[class='chakra-card css-4n73h9']")

                if len(elems) != 0:
                    for q in elems:
                        parsed_data.append(q.get_attribute("href"))
                    parsed_urls.append(cur_url)
                    json.dump(parsed_urls, open(parsed_urls_file, "w", encoding='utf-8'))
                    json.dump(parsed_data, open(result_file, "w", encoding='utf-8'))
                    break
                
                bad_count += 1
                if 'page=417' not in cur_url and bad_count <= 3 and ('moment' not in driver.title and 'момент' not in driver.title):
                    driver.refresh()
                    time.sleep(1)
                    continue
            
                if 'Unable to load results' in driver.page_source or 'page=417' in cur_url:
                    bad_urls.add(cur_url)
                    json.dump(list(bad_urls), open(bad_urls_file, 'w', encoding='utf-8'))
                    need_break = True
                    #print("Unable to load results", cur_url)
                    break
                else:
                    if bad_count <= 5 and ('moment' not in driver.title and 'момент' not in driver.title):
                        driver.refresh()
                        time.sleep(1)
                        continue

                    updater.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text='I cannot parse Vimeo any more\nHelp me!\n\n#' + HOST_NAME + " #parsing", parse_mode='HTML')
                    input("Fix me and press enter")
                    driver = get_url(driver, cur_url)
                    time.sleep(1)
                    
            page += 1
            if need_break:
                break
        except:
            pass

updater.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text='End parsing Vimeo\n\n#' + HOST_NAME + " #parsing", parse_mode='HTML')