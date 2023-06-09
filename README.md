# Modular DLR Gym Env for Robots with PyBullet

This repo implements a modular Gym env in which all relevant components, i.e. robots, obstacles, sensors and training goals, can be switched out at will without much or any change in the code.

## Dependencies

- Python 3.10
- numpy
- stable_baselines3
- pytorch
- pybullet
- pandas
- tensorboard
- zennit
- pyyaml

## State of the code & plans

For now, the basic setup with random obstacles, targets and a UR5 robot trying to reach them is implemented and hardcoded into the \_\_init\_\_ of the gym env. In the future, it will be possible to swap robots, scenarios and sensors via a config.
However, the basic capability to do that easily is already there, it just needs to be done by hand (initializing the objects and adding them to the list of sensors, robots etc.).

To implement your own sensors, scenarios, robots or goals refer to the existing implementations. Both the abstract base classes and the implementations feature explicit instructions and commentary for writing your own version.

Implemented:
- Robots:
    - movement via Inverse Kinematics, Joint Angles and Joint Velocities
    - UR5
    - Kuka KR16
- Sensors:
    - Position & Rotation
    - Joints
    - Lidar
    - Camera (floating, floating & following, mounted on EE)
- Worlds (Scenarios):
    - Random Obstacles (Yifan)
    - Table Experiment with humans and random obstacles
- Goal:
    - Position goal with collision avoidance (Yifan)

Coming soon:
- the three old testcases (one plate, two plates, moving plate)
- variations of the testcases (different directions and angles)

## Running the code

To start training or evaluation, run ```python run.py```. What will be run is determined by a few parameters in the run.py file. They are commented and explained at the top of the file.

## Pretrained Models
### Model 1 (Amir)
Generally, this approach makes use of a point cloud sensor to generate a point cloud of the obstacles in the workspace. The point clouds are abstracted as cuboids and the robot skeleton is then projected on these cuboids. The observation space stated in the original paper is extended by the robot skeleton and the mentioned projections. For the reward function we added additional terminal rewards and penalties for reaching the target and for hitting an obstacle. 
To gain further insight on the model see the “Model_1_Var_1.yaml” file in the configs folder. The file gives information on the modular sensor, world and goal classes that were used to train this model.

To test out this model run the following command: 
```python run.py configs/tableexperiment_pcr/Model_1_Var_1.yaml --eval```

A demo can be seen here: 

https://user-images.githubusercontent.com/104627562/235155347-cc490e9b-b494-441e-96c1-c685fca170d3.mp4

*Obstacle in red and end effector target in green*
