from setuptools import find_packages, setup

package_name = 'usv_recovery_planner'

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
    description='Real-time Hungarian role allocator, event-triggered LLM arbiter, and closed-loop mission verifier',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'planner_node = usv_recovery_planner.planner_node:main',
        ],
    },
)
