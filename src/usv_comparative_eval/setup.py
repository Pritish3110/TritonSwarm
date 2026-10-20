from setuptools import find_packages, setup

package_name = 'usv_comparative_eval'

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
    description='Ablated baseline swarm and impartial comparative scorecard for the architecture study',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'baseline_node = usv_comparative_eval.baseline_node:main',
            'scorecard_node = usv_comparative_eval.scorecard_node:main',
            'compare_runs = usv_comparative_eval.compare_runs:main',
        ],
    },
)
