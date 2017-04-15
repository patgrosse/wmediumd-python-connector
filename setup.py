# -*- coding: utf-8 -*-

from setuptools import setup

with open('README.md') as f:
    readme = f.read()

with open('LICENSE') as f:
    license = f.read()

setup(
    name='wmediumd-python-connector',
    version='0.1',
    description='Python Connector for the wmediumd server extension',
    long_description=readme,
    author='Patrick Grosse',
    author_email='patrick.grosse@uni-muenster.de',
    url='https://github.com/patgrosse/wmediumd-python-connector',
    license=license,
    packages=['wmediumd', 'wmediumd.data'],
    package_data={'wmediumd.data': ['signal_table_ieee80211ax']}
)
