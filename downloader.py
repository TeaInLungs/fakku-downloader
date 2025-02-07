import os
import pickle
import re
from zipfile import ZipFile, ZIP_STORED
from PIL import Image
from shutil import rmtree
from time import sleep
from typing import Optional, List

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, JavascriptException, NoSuchElementException

from bs4 import BeautifulSoup as bs
from tqdm import tqdm


BASE_URL = "https://www.fakku.net"
LOGIN_URL = f"{BASE_URL}/login/"
# If image has any dimension lower than threshold, manga is considered failed.
FAIL_THRESHOLD = 1000
# Initial display settings for headless browser. Any manga in this
# resolution will be opened correctly and with the best quality.
MAX_DISPLAY_SETTINGS = [1440, 2560]
# Path to headless driver
EXEC_PATH = "chromedriver"
# File with manga urls
URLS_FILE = "urls.txt"
# File with completed urls
DONE_FILE = "done.txt"
# File for failed urls
FAIL_FILE = "fail.txt"
# File with prepared cookies
COOKIES_FILE = "cookies.pickle"
# Root directory for manga downloader
ROOT_MANGA_DIR = "manga"
# Timeout to page loading in seconds
TIMEOUT = 5
# Wait between page loading in seconds
WAIT = 2
# Max manga to download in one session (-1 == no limit)
MAX = None
# User agent for web browser
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"

def program_exit():
    print("Program exit.")
    exit()

def sanitize_url(url: str) -> str:
    # Sanitize url.
    url = re.sub(r"\/read(\/page\/.+)?", "", url)
    return url

class FDownloader:
    """
    Class which allows download manga.
    The main idea of download - using headless browser and just saving
    screenshot from that. Because canvas in fakku.net is protected
    from download via simple .toDataURL js function etc.
    """

    def __init__(
        self,
        urls_file: str = URLS_FILE,
        done_file: str = DONE_FILE,
        fail_file: str = FAIL_FILE,
        cookies_file: str = COOKIES_FILE,
        root_manga_dir: str = ROOT_MANGA_DIR,
        driver_path:str = EXEC_PATH,
        default_display: List[int] = MAX_DISPLAY_SETTINGS,
        timeout: float = TIMEOUT,
        wait: float = WAIT,
        login: Optional[str] = None,
        password: Optional[str] = None,
        _max: Optional[int] = MAX,
        pack: Optional[bool] = False,
        viewport: Optional[bool] = False
    ):
        """
        param: urls_file -- string name of .txt file with urls
            Contains list of manga urls, that's to be downloaded
        param: done_file -- string name of .txt file with urls
            Contains list of manga urls that have successfully been downloaded
        param: cookies_file -- string name of .picle file with cookies
            Contains bynary data with cookies
        param: driver_path -- string
            Path to the headless driver
        param: default_display -- list of two int (width, height)
            Initial display settings. After loading the page, they will be changed
        param: timeout -- float
            Timeout upon waiting for first page to load
            If <5 may be poor quality.
        param: wait -- float
            Wait in seconds beetween pages downloading.
            If <1 may be poor quality.
        param: login -- string
            Login or email for authentication
        param: password -- string
            Password for authentication
        """
        self.urls_file = urls_file
        self.done_file = done_file
        self.fail_file = fail_file
        self.cookies_file = cookies_file
        self.root_manga_dir = root_manga_dir
        self.driver_path = driver_path
        self.browser = None
        self.default_display = default_display
        self.timeout = timeout
        self.wait = wait
        self.login = login
        self.password = password
        self.max = _max
        self.pack = pack
        self.viewport = viewport
        self.urls = self.__get_urls_list()

    def init_browser(self, headless: Optional[bool] = False) -> None:
        """
        Initializing browser and authenticate if necessary
        Lots of obfuscation via: https://intoli.com/blog/making-chrome-headless-undetectable/
        ---------------------
        param: headless -- bool
            If True: launch browser in headless mode(for download manga)
            If False: launch usually browser with GUI(for first authenticate)
        """
        options = webdriver.ChromeOptions()
        options.add_argument("--force-device-scale-factor=1")
        if headless:
            options.add_argument("--headless")
            options.add_argument("--window-position=-2400,-2400")
            # Silent output?
            options.add_argument("--log-level=OFF")
        options.add_argument(f"user-agent={USER_AGENT}")
        self.browser = webdriver.Chrome(
            executable_path=self.driver_path,
            chrome_options=options,
        )

        # Note: not sure if this is actually working, or needs to be called later. Tough to verify.
        customJs = """
        // overwrite the `languages` property to use a custom getter
        Object.defineProperty(navigator, 'languages', {
          get: function() {
            return ['en-US', 'en'];
          },
        });

        // overwrite the `plugins` property to use a custom getter
        Object.defineProperty(navigator, 'plugins', {
          get: function() {
            // this just needs to have `length > 0`, but we could mock the plugins too
            return [1, 2, 3, 4, 5];
          },
        });

        // Spoof renderer checks
        const getParameter = WebGLRenderingContext.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
          // UNMASKED_VENDOR_WEBGL
          if (parameter === 37445) {
            return 'Intel Open Source Technology Center';
          }
          // UNMASKED_RENDERER_WEBGL
          if (parameter === 37446) {
            return 'Mesa DRI Intel(R) Ivybridge Mobile ';
          }

          return getParameter(parameter);
        };
        """

        self.browser.execute_script(customJs)

        if not headless:
            self.__auth()
        self.__set_cookies()
        self.browser.set_window_size(*self.default_display)

    def __set_cookies(self) -> None:
        self.browser.get(LOGIN_URL)
        with open(self.cookies_file, "rb") as f:
            cookies = pickle.load(f)
            for cookie in cookies:
                if "expiry" in cookie:
                    cookie["expiry"] = int(cookie["expiry"])
                    self.browser.add_cookie(cookie)

    def __init_headless_browser(self) -> None:
        """
        Recreating browser in headless mode(without GUI)
        """
        options = Options()
        options.headless = True
        self.browser = webdriver.Chrome(
            executable_path=self.driver_path, chrome_options=options
        )

    def __auth(self) -> None:
        """
        Authentication in browser with GUI for saving cookies in first time
        """
        self.browser.get(LOGIN_URL)
        if not (self.login is None and self.password is None):
            self.browser.find_element_by_id("username").send_keys(self.login)
            self.browser.find_element_by_id("6ccb8078a7").send_keys(self.password)
            self.browser.find_element_by_class_name("js-submit-login").click()

        ready = input("Tab Enter to continue after you login...")
        with open(self.cookies_file, "wb") as f:
            pickle.dump(self.browser.get_cookies(), f)

        self.browser.close()
        # Recreating browser in headless mode for next manga downloading
        self.__init_headless_browser()

    def set_viewport_size(self, width, height):
        # https://stackoverflow.com/questions/37181403/how-to-set-browser-viewport-size
        # print(f"img resize {width} {height}")
        test = self.browser.execute_script("""
            return [window.outerWidth,  window.innerWidth,
            window.outerHeight,  window.innerHeight];
            """)
        # print(test)
        window_size = self.browser.execute_script("""
            return [window.outerWidth - window.innerWidth + arguments[0],
            window.outerHeight - window.innerHeight + arguments[1]];
            """, width, height)
        # print(window_size)
        self.browser.set_window_size(*window_size)

    def load_all(self) -> None:
        """
        Just main function which opening each page and save it in .png
        """

        self.browser.set_window_size(*self.default_display)
        if not os.path.exists(self.root_manga_dir):
            os.mkdir(self.root_manga_dir)


        urls_processed = 0
        for url in self.urls:
            # If `url` is an empty string, skip it.
            if not url.strip():
                continue

            # Sanitize url.
            url = sanitize_url(url)

            self.browser.get(url)
            self.waiting_loading_page(is_reader_page=False)
            try:
                page_count = self.__get_page_count(self.browser.page_source)
            except ValueError:
                self.add_failed(url)
                continue

            manga_name = url.split("/")[-1]
            manga_folder = os.sep.join([self.root_manga_dir, manga_name])
            if not os.path.exists(manga_folder):
                os.mkdir(manga_folder)

            print(f'Downloading "{manga_name}" manga.')
            delay_before_fetching = True  # When fetching the first page, multiple pages load and the reader slows down

        
            bar_format="{desc}: {percentage:3.0f}% |{bar}| {n:3.0f}/{total:3.0f}"
            ascii=" ="
            for page_num in tqdm(range(1, page_count + 1), ascii=ascii, bar_format=bar_format):
                destination_file = os.sep.join([manga_folder, f"{page_num}.png"])
                if os.path.isfile(destination_file):
                    delay_before_fetching = True  # When skipping files, the reader will load multiple pages and slow down again
                    continue

                self.browser.get(f"{url}/read/page/{page_num}")
                self.waiting_loading_page(
                    is_reader_page=True, should_add_delay=delay_before_fetching
                )
                delay_before_fetching = False

                # Count of leyers may be 2 or 3 therefore we get different target layer
                n = self.browser.execute_script(
                    "return document.getElementsByClassName('layer').length"
                )
                try:
                    # Resizing window size for exactly manga page size
                    width = self.browser.execute_script(
                        f"return document.getElementsByTagName('canvas')[{n-2}].width"
                    )
                    height = self.browser.execute_script(
                        f"return document.getElementsByTagName('canvas')[{n-2}].height"
                    )
                    if self.viewport:
                        self.set_viewport_size(width, height)
                    else: 
                        self.browser.set_window_size(width, height)

                except JavascriptException:
                    print(
                        "\nSome error with JS. Page source are note ready. You can try increase argument -t"
                    )

                # Delete all UI and save page
                self.browser.execute_script(
                    f"document.getElementsByClassName('layer')[{n-1}].remove()"
                )
                
                self.browser.save_screenshot(destination_file)

            # Check every file page for size.
            failed = False
            for page_num in range(1, page_count + 1):
                destination_file = os.sep.join([manga_folder, f"{page_num}.png"])
                img = Image.open(destination_file)
                if img.width < FAIL_THRESHOLD or img.height < FAIL_THRESHOLD:
                    failed = True
                img.close()
            if failed: 
                self.add_failed(url)
                self.remove_manga_folder(manga_folder, page_count)
                # Reinit browser!
                self.browser.close()
                self.init_browser(headless=True)
                continue

            if self.pack:
                zipname = os.sep.join([self.root_manga_dir , f"{manga_name}.cbz"])
                with ZipFile(zipname, "w") as archive:
                    for page_num in range(1, page_count + 1):
                        file = os.sep.join([manga_folder, f"{page_num}.png"])
                        archive.write(file, f"{page_num}.png", None, ZIP_STORED)
                if os.path.exists(zipname):
                    self.remove_manga_folder(manga_folder, page_count)

            self.add_done(url)
            urls_processed += 1
            if self.max is not None and urls_processed >= self.max:
                break

    def remove_manga_folder(self, manga_folder: str, page_count: int):
        for page_num in range(1, page_count + 1):
            file = os.sep.join([manga_folder, f"{page_num}.png"])
            if os.path.exists(file):
                os.remove(file)
        os.rmdir(manga_folder)

    def add_done(self, url: str):
        # print(f"Manga done! \[T]/")
        file_obj = open(self.done_file, "a")
        file_obj.write(f"{url}\n")
        file_obj.close()

    def add_failed(self, url: str):
        print(f"Error: Failed {url}")
        fail_file_obj = open(self.fail_file, "a")
        fail_file_obj.write(f"{url}\n")
        fail_file_obj.close()

    def load_urls_from_collection(self, collection_url: str) -> None:
        """
        Function which records the manga URLs inside a collection
        """
        self.browser.get(collection_url)
        self.waiting_loading_page(is_reader_page=False)
        page_count = self.__get_page_count_in_collection(self.browser.page_source)
        with open(self.urls_file, "a") as f:
            for page_num in tqdm(range(1, page_count + 1)):
                # Fencepost problem, the first page of a collection is already loaded
                if page_num != 1:
                    self.browser.get(f"{collection_url}/page/{page_num}")
                    self.waiting_loading_page(is_reader_page=False)
                soup = bs(self.browser.page_source, "html.parser")
                for div in soup.find_all("div", attrs={"class": "col-comic"}):
                    f.write(f"{BASE_URL}{div.find('a')['href']}\n")

    def __get_page_count(self, page_source: str) -> int:
        """
        Get count of manga pages from html code
        ----------------------------
        param: page_source -- string
            String that contains html code
        return: int
            Number of manga pages
        """
        # print(type(page_source))
        match = re.search(r"\"\>(\d+) page(s?)\<\/div\>", page_source)
        if match:
            return int(match.group(1))
        else:
            raise ValueError("Page count are not found.")

    def __get_page_count_in_collection(self, page_source: str) -> int:
        """
        Get count of collection pages from html code
        ----------------------------
        param: page_source -- string
            String that contains html code
        return: int
            Number of collection pages
        """
        soup = bs(page_source, "html.parser")
        page_count = 1
        try:
            # Search for page links
            page_links=soup.find_all('a', {'href': re.compile(r"/page/(\d+)")})
 
            # If there are multiple pages...
            if len(page_links) > 0:
                # Find the maximum page number listed in link URLs
                page_count=max([
                        int(re.search(r"\/page\/(\d+)", pg['href']).group(1))
                        for pg in page_links
                    ])
        except Exception as ex:
            print(ex)
        return page_count

    def __get_urls_list(self) -> List[str]:
        """
        Get list of urls from .txt file
        --------------------------
        return: urls -- list
            List of urls from urls_file
        """
        done = []
        with open(self.done_file, "r") as donef:
            for line in donef:
                done.append(line.replace("\n", ""))
                
        failed = []
        with open(self.fail_file, "r") as failf:
            for line in failf:
                failed.append(line.replace("\n", ""))

        urls = []
        with open(self.urls_file, "r") as f:
            for line in f:
                clean_line = line.replace("\n", "")
                clean_line = sanitize_url(clean_line)
                if clean_line not in done and clean_line not in failed and clean_line not in urls:
                    urls.append(clean_line)
        return urls

    def waiting_loading_page(
        self,
        is_reader_page: bool = False,
        should_add_delay: bool = False,
    ) -> None:
        """
        Awaiting while page will load
        ---------------------------
        param: is_non_reader_page -- bool
            False -- awaiting of main manga page
            True -- awaiting of others manga pages
        param: should_add_delay -- bool
            False -- the page num != 1
            True -- this is the first page, we need to wait longer to get good quality
        """
        if not is_reader_page:
            sleep(self.wait)
            elem_xpath = "//link[@rel='icon']"
            iframe = False
        elif should_add_delay:
            sleep(self.wait * 3)
            elem_xpath = "//div[@data-name='PageView']"
            iframe = True
        else:
            sleep(self.wait)
            elem_xpath = "//div[@data-name='PageView']"
            iframe = True
        try:
            # We need to switch into iframe before looking for canvas.
            if iframe:
                try:
                    self.browser.switch_to.frame(self.browser.find_element_by_tag_name("iframe"))
                except NoSuchElementException:
                    print("\nError: Couldn't find iframe. Bugger.")
                    program_exit()
            else: 
                self.browser.switch_to_default_content()

            element = EC.presence_of_element_located((By.XPATH, elem_xpath))
            WebDriverWait(self.browser, self.timeout).until(element)
        except TimeoutException:
            print(
                "\nError: timed out waiting for page to load. + \
                You can try increase param -t for more delaying."
            )
            program_exit()
