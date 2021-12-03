#
# (c) W6BSD Fred Cirera
#

import ds18x20
import gc
import network
import onewire
import os
import time
import uasyncio as asyncio
import ujson
import uselect as select
import usocket as socket
import ustruct as struct

from machine import Pin
from machine import RTC
from machine import WDT
from machine import reset

import logging

import wificonfig as wc

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("Belkin")

HTML_PATH = b'/html'

HTML_ERROR = """<!DOCTYPE html><html><head><title>404 Not Found</title>
<body><h1>{} {}</h1></body></html>
"""

HTTPCodes = {
  200: ('OK', 'OK'),
  303: ('Moved', 'Moved'),
  307: ('Temporary Redirect', 'Moved temporarily'),
  400: ('Bad Request', 'Bad request'),
  404: ('Not Found', 'File not found'),
  500: ('Internal Server Error', 'Server erro'),
}

MIME_TYPES = {
  b'css': 'text/css',
  b'html': 'text/html',
  b'js': 'application/javascript',
  b'json': 'application/json',
  b'txt': 'text/plain',
  b'png': 'image/png',
}

MAX_TEMP = 40.0

# (date(2000, 1, 1) - date(1900, 1, 1)).days * 24*60*60
NTP_DELTA = 3155673600

def get_ntp_time(host):
  NTP_QUERY = bytearray(48)
  NTP_QUERY[0] = 0x1b
  addr = socket.getaddrinfo(host, 123)[0][-1]
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  s.settimeout(2)
  try:
    res = s.sendto(NTP_QUERY, addr)
    msg = s.recv(res)
  finally:
    s.close()

  val = struct.unpack("!I", msg[40:44])[0]
  return val - NTP_DELTA


# There's currently no timezone support in MicroPython, so
# time.localtime() will return UTC time (as if it was .gmtime())
# add timezone org, default -7 for California
def settime(timezone=-7, server='us.pool.ntp.org'):
  try:
    time_ntp = get_ntp_time(server)
  except OSError:
    LOG.warning("Error fetching NTP time")
    return

  time_ntp = time_ntp + (timezone * 60 * 60)
  tm = time.localtime(time_ntp)
  RTC().datetime((tm[0], tm[1], tm[2], tm[6] + 1, tm[3], tm[4], tm[5], 0))


def parse_headers(head_lines):
  headers = {}
  for line in head_lines:
    if line.startswith(b'GET') or line.startswith(b'POST'):
      method, uri, proto = line.split()
      headers[b'Method'] = method
      headers[b'URI'] = uri
      headers[b'Protocol'] = proto
    else:
      try:
        key, val = line.split(b":", 1)
        headers[key] = val
      except:
        LOG.warning('header line warning: %s', line)
  return headers


class Relay:

  def __init__(self, *args, **kwargs):
    self.pin = Pin(*args, **kwargs)
    self.forced = False         # False for Automatic, True for forced

  def value(self, val=None):
    if val is None:
      return self.pin.value()
    return self.pin.value(val)

  def on(self):
    if self.pin.value() == 1:
      return
    self.pin.value(1)

  def off(self):
    if self.pin.value() == 0:
      return
    return self.pin.value(0)


class DS1820:

  def __init__(self, *args, **kwargs):
    self.pin = Pin(*args, **kwargs)
    self.sensor = ds18x20.DS18X20(onewire.OneWire(self.pin))
    self.temp = 0
    self.lastread = 0
    self.rom = None
    roms = self.sensor.scan()
    if not roms:
      LOG.error('DS1820 sensor not found')
      return

    self.rom = roms[0]
    LOG.info('Found DS devices: %s', self.rom)

  async def read(self):
    if not self.rom:
      LOG.warning('DS1820 sensor not found')
      return 0.0

    LOG.debug('Read DS1820 temp')
    self.sensor.convert_temp()
    await asyncio.sleep_ms(750)
    self.temp = self.sensor.read_temp(self.rom)
    return self.temp


class Server:

  def __init__(self, switch, temp, addr='0.0.0.0', port=80):
    self.addr = addr
    self.port = port
    self.open_socks = []
    self.switch = switch
    self.temp = temp
    self._files = [bytes('/' + f, 'utf-8') for f in os.listdir('html')]

  async def run(self, loop):
    addr = socket.getaddrinfo(self.addr, self.port, 0, socket.SOCK_STREAM)[0][-1]
    s_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s_sock.bind(addr)
    s_sock.listen(5)
    self.open_socks.append(s_sock)
    LOG.info('Awaiting connection on %s:%d', self.addr, self.port)

    poller = select.poll()
    poller.register(s_sock, select.POLLIN)
    while True:
      if poller.poll(1):  # 1ms
        c_sock, addr = s_sock.accept()  # get client socket
        LOG.debug('Connection from %s:%d', *addr)
        loop.create_task(self.process_request(c_sock))
        gc.collect()
      await asyncio.sleep_ms(100)

  async def process_request(self, sock):
    LOG.debug('Process request %s', sock)
    self.open_socks.append(sock)
    sreader = asyncio.StreamReader(sock)
    swriter = asyncio.StreamWriter(sock, '')
    try:
      head_lines = []
      while True:
        line = await sreader.readline()
        line = line.rstrip()
        if line in (b'', b'\r\n'):
          break
        head_lines.append(line)

      headers = parse_headers(head_lines)
      uri = headers.get(b'URI')
      if not uri:
        LOG.debug('Empty request')
        raise OSError

      LOG.debug('Request %s %s', headers[b'Method'].decode(), uri.decode())
      if uri == b'/api/v1/status':
        data = await self.get_state()
        await self.send_json(swriter, data)
      elif uri == b'/api/v1/on':
        self.switch.on()
        self.switch.forced = True
        data = await self.get_state()
        await self.send_json(swriter, data)
      elif uri == b'/api/v1/off':
        self.switch.off()
        self.switch.forced = True
        data = await self.get_state()
        await self.send_json(swriter, data)
      elif uri == b'/api/v1/auto':
        self.switch.forced = False
        data = await self.get_state()
        await self.send_json(swriter, data)
      elif uri.startswith('/api/v1/reboot'):
        await self.reboot(swriter)
      elif uri == b'/':
        await self.send_file(swriter, b'/index.html')
      elif uri in self._files:
        await self.send_file(swriter, uri)
      else:
        await self.send_error(swriter, 404)
    except OSError:
      pass

    LOG.debug("%r", self.switch)
    gc.collect()
    LOG.debug('Disconnecting %s / %d', sock, len(self.open_socks))
    sock.close()
    self.open_socks.remove(sock)

  async def get_state(self):
    data = {}
    data['time'] = "{:02d}:{:02d}".format(*time.localtime()[3:5])
    data['forced'] = self.switch.forced;
    data['switch'] = self.switch.value()
    data['temp'] = await self.temp.read()
    return data

  async def send_json(self, wfd, data):
    LOG.debug('send_json')
    jdata = ujson.dumps(data)
    await wfd.awrite(self._headers(200, b'json', content_len=len(jdata)))
    await wfd.awrite(jdata)
    gc.collect()

  async def send_file(self, wfd, url):
    fpath = b'/'.join([HTML_PATH, url.lstrip(b'/')])
    mime_type = fpath.split(b'.')[-1]
    LOG.debug('send_file: %s mime_type: %s', url, mime_type)
    try:
      with open(fpath, 'rb') as fd:
        await wfd.awrite(self._headers(200, mime_type, cache=-1))
        for line in fd:
          await wfd.awrite(line)
    except OSError as err:
      LOG.debug('send file error: %s %s', err, url)
      await self.send_error(wfd, 404)
    gc.collect()

  async def send_error(self, wfd, err_c):
    if err_c not in HTTPCodes:
      err_c = 400
    errors = HTTPCodes[err_c]
    await wfd.awrite(self._headers(err_c) + HTML_ERROR.format(err_c, errors[1]))
    gc.collect()

  async def send_redirect(self, wfd, location='/'):
    page = HTML_ERROR.format(303, 'redirect')
    await wfd.awrite(self._headers(303, location=location, content_len=len(page)))
    await wfd.awrite(HTML_ERROR.format(303, 'redirect'))
    gc.collect()

  def close(self):
    LOG.debug('Closing %d sockets', len(self.open_socks))
    for sock in self.open_socks:
      sock.close()

  async def reboot(self, wfd):
    jdata = ujson.dumps({"status": "reboot"})
    await wfd.awrite(self._headers(200, b'json', content_len=len(jdata)))
    await wfd.awrite(jdata)
    await asyncio.sleep_ms(500)
    reset()

  @staticmethod
  def _headers(code, mime_type=None, location=None, content_len=0, cache=None):
    try:
      labels = HTTPCodes[code]
    except KeyError:
      raise KeyError('HTTP code (%d) not found', code)
    headers = []
    headers.append(b'HTTP/1.1 {:d} {}'.format(code, labels[0]))
    headers.append(b'Content-Type: {}'.format(MIME_TYPES.get(mime_type, 'text/html')))
    if location:
      headers.append(b'Location: {}'.format(location))
    if content_len:
      headers.append(b'Content-Length: {:d}'.format(content_len))

    if cache and cache == -1:
      headers.append(b'Cache-Control: public, max-age=604800, immutable')
    elif cache and isinstance(cache, str):
      headers.append(b'Cache-Control: '.format(cache))
    headers.append(b'Connection: close')

    return b'\n'.join(headers) + b'\n\n'


def wifi_connect(ssid, password):
  ap_if = network.WLAN(network.AP_IF)
  ap_if.active(False)
  sta_if = network.WLAN(network.STA_IF)
  if not sta_if.isconnected():
    sta_if.active(True)
    sta_if.connect(ssid, password)
    for cnd in range(10):
      LOG.info('Connecting to WiFi...')
      if sta_if.isconnected():
        break
      time.sleep(3)
    if not sta_if.isconnected():
      LOG.error('Wifi connection error sleeping for 30 second before reboot')
      time.sleep(30)
      reset()

  LOG.info('Network config: %s', sta_if.ifconfig())
  gc.collect()
  return sta_if


async def automation(tm_on, switch, temp):
  # If the current hour/min is in the tm_on set the realy
  # is closed else the relay is open.
  while True:
    await asyncio.sleep_ms(10061)
    if switch.forced:
      continue
    t = time.localtime()
    hour, min = t[3:5]
    key = int("{:d}{:02d}".format(hour, min))
    if key in tm_on and await temp.read() < MAX_TEMP:
      switch.on()
    else:
      switch.off()


async def update_rtc(tzone):
  # Re-sync the micro-controller internal RTC with NTP every hours.
  settime(timezone=tzone)
  while True:
    await asyncio.sleep(1823)
    settime(timezone=tzone)
    gc.collect()


async def monitor(switch, temp):
  while True:
    if switch.forced and switch.value() == 1 and await temp.read() > MAX_TEMP:
      switch.forced = False
      switch.off()
    await asyncio.sleep_ms(10061)  # 10061 – super-prime, happy prime (10sec)


async def heartbeat():
  feed_time = 1500
  wdt = WDT()
  while True:
    wdt.feed()
    await asyncio.sleep_ms(feed_time)


def parse_dat(filename):
  global MAX_TEMP
  sched_data = {
    'tz': -7,
    'tm_on': []
  }

  def _int(value, default):
    try:
      return int(value)
    except ValueError as err:
      pass
    return default

  try:
    line_no = 0
    with open(filename) as fd:
      for line in fd:
        line_no += 1
        line = line.rstrip()
        if not line or line.startswith('#'):
          continue
        if line.startswith('@maxtemp'):
          _, value = line.split()
          MAX_TEMP = _int(value, MAX_TEMP)
          LOG.info('Max temp: %d', MAX_TEMP)
        elif line.startswith('@timezone'):
          _, value = line.split()
          sched_data['tz'] = _int(value, -7)
          LOG.info('Time zone: %d', sched_data['tz'])
        else:
          value = _int(line, -1)
          if value < 0:
            LOG.info('Value error: %s line: %d', line, line_no)
          else:
            sched_data['tm_on'].append(value)
  except Exception as err:
    LOG.info('No scheduling "time.dat" file read error %s', err)
    time.sleep(300)

  LOG.info(sched_data['tm_on'])
  return sched_data

def main():
  wifi = wifi_connect(wc.SSID, wc.PASSWORD)
  try:
    import update
    update.timesfile()
  except:
    print("Update error")

  switch = Relay(2, Pin.OUT, value=1)
  ds1820 = DS1820(0, Pin.IN, Pin.PULL_UP)

  sched_data = parse_dat('times.dat')
  LOG.info('Last chance to press [^C]')
  time.sleep(7)
  LOG.info('Start server')
  server = Server(switch, ds1820)
  loop = asyncio.get_event_loop()
  loop.create_task(update_rtc(sched_data['tz']))
  loop.create_task(heartbeat())
  loop.create_task(automation(sched_data['tm_on'], switch, ds1820))
  loop.create_task(server.run(loop))
  loop.create_task(monitor(switch, ds1820))

  try:
    loop.run_forever()
  except KeyboardInterrupt:
    LOG.info('Closing all connections')


if __name__ == "__main__":
 main()
