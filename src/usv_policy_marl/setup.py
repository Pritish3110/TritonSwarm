from setuptools import find_packages, setup

package_name = 'usv_policy_marl'

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
    description='Frozen FA-MAPPO multi-agent policy executor node in ROS 2',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'policy_executor_node = usv_policy_marl.policy_executor_node:main',
        ],
    },
)
