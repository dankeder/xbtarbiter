from setuptools import setup, find_packages
from subprocess import Popen, PIPE


def get_version():
    """
    Get version from PKG-INFO file.
    """
    try:
        # Try to get version from the PKG-INFO file
        f = open('PKG-INFO', 'r')
        for line in f.readlines():
            if line.startswith('Version: '):
                return line.split(' ')[1].strip()
    except IOError:
        # Try to get the version from the latest git tag
        p = Popen(['git', 'describe', '--tags'], stdout=PIPE, stderr=PIPE)
        p.stderr.close()
        line = p.stdout.readlines()[0]
        return line.strip()

setup(name='xbtarbiter',
        version=get_version(),
        description='BTC arbiter',
        long_description='BTC arbiter',
        author='Dan Keder',
        author_email='dan.keder@gmail.com',
        packages=find_packages(),
        include_package_data=True,
        zip_safe=False,
        install_requires=[
                'requests',
                'python-gnupg',
                'docopt',
            ],
        entry_points={
                'console_scripts': [
                    'xbtarbiter= xbtarbiter:main',
                ]
            }
    )

# vim: expandtab
