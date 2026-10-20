from setuptools import find_packages, setup

package_name = 'usv_perception_mesh'

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
    description='Sensor noise, EKF target state estimation and ad-hoc communication mesh simulation',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'perception_node = usv_perception_mesh.perception_node:main',
        ],
    },
)
