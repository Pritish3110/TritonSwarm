from setuptools import find_packages, setup

package_name = 'usv_health_diagnosis'

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
    description='Real-time telemetry feature extraction and frozen XGBoost fault diagnosis node',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'diagnosis_node = usv_health_diagnosis.diagnosis_node:main',
        ],
    },
)
