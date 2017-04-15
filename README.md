# wmediumd-python-connector

Python Connector for the wmediumd server extension

See [patgrosse/wmediumd](https://github.com/patgrosse/wmediumd)

## History

I initially created this for [Mininet-WiFi](https://github.com/intrig-unicamp/mininet-wifi) and later decided to create a standalone version too.

## Install

```bash
# May require sudo
python setup.py install
```

## Usage

```python
from wmediumd.wmediumdPyConnector import WmediumdManager

# Do your stuff eg. start/connect to wmediumd:
WmediumdManager.connect()
```