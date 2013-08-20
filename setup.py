from setuptools import setup

setup(
    name='myblog',
    version='1.0.1',
    description='MyBlog - OpenShift App',
    author='Kamal Mustafa',
    author_email='kamal.mustafa@gmail.com',
    url='http://www.python.org/sigs/distutils-sig/',
    install_requires=[
        'Django>=1.5.2',
        'mezzanine>=1.4.10',
        'Pillow',
        'django_compressor==1.3'
    ],
)
