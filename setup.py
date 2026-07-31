from setuptools import setup, find_packages

setup(
    name="indiascreen",
    version="0.1.0",
    description="Free, open-source NSE/BSE stock screener with technical, fundamental & backtesting",
    author="Your Name",
    license="MIT",
    packages=find_packages(),
    install_requires=[
        "yfinance>=0.2.36",
        "pandas>=2.0.0",
        "numpy>=1.24.0",
        "pandas-ta>=0.3.14b",
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.0",
        "streamlit>=1.32.0",
    ],
    entry_points={
        "console_scripts": [
            "indiascreen=screener.scanner:cli",
        ],
    },
    python_requires=">=3.10",
)
