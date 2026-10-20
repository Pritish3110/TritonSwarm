from setuptools import find_packages, setup

package_name = 'usv_sim_core'

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
    description='Native 3-DOF multi-USV and evasive target simulation node with RViz2 telemetry',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'sim_node = usv_sim_core.sim_node:main',
        ],
    },
)
