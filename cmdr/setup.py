from setuptools import setup

setup(
    name='cmdr',
    version='0.1',
    py_modules=['cmdr'],
    install_requires=[
        'Click'
    ],
    entry_points='''
        [console_scripts]
        cmdr=cmdr:cli
    ''',
)