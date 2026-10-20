from setuptools import find_packages, setup

package_name = 'usv_safety_shield'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Pritish Bhatasana',
    maintainer_email='pritish@todo.todo',
    description='Control Barrier Function (CBF) safety shield filter node enforcing collision avoidance',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'safety_node = usv_safety_shield.safety_node:main',
        ],
    },
)
