import gym
import numpy as np
import pybullet as pyb
from time import time
import pandas as pd

# import abstracts
from robot.robot import Robot
from sensor.sensor import Sensor
from goal.goal import Goal
from world.world import World

# import implementations, new ones hav to be added to the registries to work
#   worlds
from world import WorldRegistry
#   robots
from robot import RobotRegistry
#   sensors
from sensor import SensorRegistry
#   goals
from goal import GoalRegistry

class ModularDRLEnv(gym.Env):

    def __init__(self, env_config):
        
        #   general env attributes
        # run mode
        self.train = env_config["train"]
        # flag for normalizing observations
        self.normalize_observations = env_config["normalize_observations"]
        # flag for normalizing rewards
        self.normalize_rewards = env_config["normalize_rewards"]
        # flag for rendering
        self.display = env_config["display"]
        # flag for rendering auxillary geometry spawned by the scenario
        self.show_auxillary_geometry_world = env_config["display_extra"]
        # flag for rendering auxillary geometry spawned by the goals
        self.show_auxillary_geometry_goal = env_config["display_extra"]
        # maximum steps in an episode before timeout
        self.max_steps_per_episode = env_config["max_steps_per_episode"]
        # number of episodes after which the code will exit on its own, if set to -1 will continue indefinitely until stopped from the outside
        self.max_episodes = env_config["max_episodes"]  
        # 0: no logging, 1: logging for console every episode, 2: logging for console every episode and to csv after maximum number of episodes has been reached or after every episode if max_episodes is -1
        self.logging = env_config["logging"] 
        # whether to use static PyBullet teleporting or actually let sim time pass in its simulation
        self.use_physics_sim = env_config["use_physics_sim"]  
        # length of the stat arrays in terms of episodes over which the average will be drawn for logging
        self.stat_buffer_size = env_config["stat_buffer_size"]  
        # in seconds -> inverse is frame rate in Hz
        self.sim_step = env_config["sim_step"]  

        # tracking variables
        self.episode = 0
        self.steps_current_episode = 0
        self.sim_time = 0
        self.cpu_time = 0
        self.cpu_epoch = time()
        self.log = []
        # fill the stats with a few entries to make early iterations more robust
        self.success_stat = [False, False, False, False]
        self.out_of_bounds_stat = [False, False, False, False]
        self.timeout_stat = [False, False, False, False]
        self.collision_stat = [False, False, False, False]
        self.goal_metrics = []
        self.reward = 0
        self.reward_cumulative = 0

        # set up the PyBullet client
        disp = pyb.DIRECT if not self.display else pyb.GUI
        pyb.connect(disp)
        pyb.configureDebugVisualizer(pyb.COV_ENABLE_SHADOWS,0)
        pyb.setAdditionalSearchPath("./assets/")
        if self.use_physics_sim:
            pyb.setTimeStep(self.sim_step)

        # init world from config
        world_type = env_config["world"]["type"]
        world_config = env_config["world"]["config"]
        world_config["sim_step"] = self.sim_step
        
        self.world = WorldRegistry.get(world_type)(world_config)

        # init robots and their associated sensors and goals from config
        self.robots = []
        self.sensors = []
        self.goals = []
        id_counter = 0
        for robo_entry_outer in env_config["robots"]:
            robo_entry = env_config["robots"][robo_entry_outer]
            robo_type = robo_entry["type"]
            robo_config = robo_entry["config"]
            # add some necessary attributes
            robo_config["id"] = id_counter
            robo_config["use_physics_sim"] = self.use_physics_sim
            robo_config["world"] = self.world
            robo_config["sim_step"] = self.sim_step
            id_counter += 1
            robot = RobotRegistry.get(robo_type)(robo_config)
            self.robots.append(robot)

            # create the two mandatory sensors
            joint_sens_config = {"normalize": self.normalize_observations, "add_to_observation_space": True, 
                                 "add_to_logging": False, "sim_step": self.sim_step, "update_steps": 1, "robot": robot}
            posrot_sens_config = {"normalize": self.normalize_observations, "add_to_observation_space": False,
                                 "add_to_logging": False, "sim_step": self.sim_step, "update_steps": 1, "robot": robot,
                                 "link_id": robot.end_effector_link_id, "quaternion": True}
            new_rob_joints_sensor = SensorRegistry.get("Joints")(joint_sens_config)
            new_rob_posrot_sensor = SensorRegistry.get("PositionRotation")(posrot_sens_config)
            robot.set_joint_sensor(new_rob_joints_sensor)
            robot.set_position_rotation_sensor(new_rob_posrot_sensor)
            self.sensors.append(new_rob_posrot_sensor)
            self.sensors.append(new_rob_joints_sensor)

            # create the sensors indicated by the config
            if "sensors" in robo_entry:
                for sensor_entry_outer in robo_entry["sensors"]:
                    sensor_entry = robo_entry["sensors"][sensor_entry_outer]
                    sensor_type = sensor_entry["type"]
                    sensor_config = sensor_entry["config"]
                    sensor_config["sim_step"] = self.sim_step
                    sensor_config["robot"] = robot
                    sensor_config["normalize"] = self.normalize_observations
                    # deal with robot bound sensors that refer to other robots
                    if "target_robot" in sensor_config:
                        # go through the list of existing robots
                        for other_robot in self.robots:
                            # find the one whose name matches the target
                            if other_robot.name == sensor_config["target_robot"]:
                                sensor_config["target_robot"] = other_robot
                                break
                    new_sensor = SensorRegistry.get(sensor_type)(sensor_config)
                    robot.sensors.append(new_sensor)
                    self.sensors.append(new_sensor)
            
            if "goal" in robo_entry:
                # create the goal indicated by the config
                goal_type = robo_entry["goal"]["type"]
                goal_config = robo_entry["goal"]["config"]
                goal_config["robot"] = robot
                goal_config["train"] = self.train
                goal_config["max_steps"] = self.max_steps_per_episode
                new_goal = GoalRegistry.get(goal_type)(goal_config)
                self.goals.append(new_goal)
                robot.set_goal(new_goal)

        # init sensors that don't belong to a robot
        if "sensors" in env_config:
            for sensor_entry in env_config["sensors"]:
                sensor_type = sensor_entry["type"]
                sensor_config = sensor_entry["config"]
                sensor_config["sim_step"] = self.sim_step
                sensor_config["normalize"] = self.normalize_observations
                new_sensor = SensorRegistry.get(sensor_type)(sensor_config)
                self.sensors.append(new_sensor)

        # register robots with the world
        self.world.register_robots(self.robots)

        # construct observation space from sensors and goals
        # each sensor and goal will add elements to the observation space with fitting names
        observation_space_dict = dict()
        for sensor in self.sensors:
            if sensor.add_to_observation_space:
                observation_space_dict = {**observation_space_dict, **sensor.get_observation_space_element()}  # merges the two dicts
        for goal in self.goals:
            if goal.add_to_observation_space:
                observation_space_dict = {**observation_space_dict, **goal.get_observation_space_element()}
        self.observation_space = gym.spaces.Dict(observation_space_dict)

        # construct action space from robots
        # the action space will be a vector with the length of all robot's control dimensions added up
        # e.g. if one robot needs 4 values for its control and another 6,
        # the action space will be a 10-vector with the first 4 elements working for robot 1 and the last 6 for robot 2
        self.action_space_dims = []
        for idx, robot in enumerate(self.robots):
            joints_dims, ik_dims = robot.get_action_space_dims()
            if robot.control_mode:  # aka if self.control_mode[idx] == 1 or == 2
                self.action_space_dims.append(joints_dims)
            else:  # == 0
                self.action_space_dims.append(ik_dims)
        
        self.action_space = gym.spaces.Box(low=-1, high=1, shape=(sum(self.action_space_dims),), dtype=np.float32)

    def reset(self):
        # end execution if max episodes is reached
        if self.max_episodes != -1 and self.episode >= self.max_episodes:
            exit(0)

        # disable rendering for the setup to save time
        #pyb.configureDebugVisualizer(pyb.COV_ENABLE_RENDERING, 0)

        # reset the tracking variables
        self.steps_current_episode = 0
        self.sim_time = 0
        self.cpu_time = 0
        self.cpu_epoch = time()
        self.reward = 0
        self.reward_cumulative = 0
        self.episode += 1
        if self.max_episodes == -1:  # if we have a finite amount of episodes, we want the log to hold everything, otherwise flush it for the next one
            self.log = []  

        # build the world and robots
        # this is put into a loop that will only break if the generation process results in a collision free setup
        # the code will abort if even after several attempts no valid starting setup is found
        # TODO: maybe find a smarter way to do this
        reset_count = 0
        while True:
            if reset_count > 1000:
                raise Exception("Could not find collision-free starting setup after 1000 tries. Maybe check your world generation code.")

            # reset PyBullet
            pyb.resetSimulation()

            # reset world attributes
            self.world.reset(np.average(self.success_stat))

            # spawn robots in world
            for robot in self.robots:
                robot.build()

            # get a set of starting positions for the end effectors
            ee_starting_points = self.world.create_ee_starting_points()
            
            # get position and rotation goals
            position_targets = self.world.create_position_target()
            rotation_targets = self.world.create_rotation_target()

            # spawn world objects
            self.world.build()

            # set the robots into the starting positions
            for idx, robot in enumerate(self.robots):
                if ee_starting_points[idx][0] is None:
                    continue  # nothing to do here
                elif ee_starting_points[idx][1] is None:
                    # only position
                    self.robots[idx].moveto_xyz(ee_starting_points[idx][0], False)
                else:
                    # both position and rotation
                    self.robots[idx].moveto_xyzquat(ee_starting_points[idx][0], ee_starting_points[idx][1], False)
            
            # check collision
            self.world.perform_collision_check()
            if not self.world.collision:
                break
            else:
                reset_count += 1

        # set all robots to active
        self.active_robots = [True for robot in self.robots]

        # reset the sensors to start settings
        for sensor in self.sensors:
            sensor.reset()

        # call the goals' update routine and get their metrics, if they exist
        self.goal_metrics = []
        for goal in self.goals:
            self.goal_metrics.append(goal.on_env_reset(np.average(self.success_stat), self.episode))

        # render non-essential visual stuff
        if self.show_auxillary_geometry_world:
            self.world.build_visual_aux()
        if self.show_auxillary_geometry_goal:
            for goal in self.goals:
                goal.build_visual_aux()

        # turn rendering back on
        pyb.configureDebugVisualizer(pyb.COV_ENABLE_RENDERING, 1)

        return self._get_obs()

    def _get_obs(self):
        obs_dict = dict()
        # get the sensor data
        for sensor in self.sensors:
            if sensor.add_to_observation_space:
                obs_dict = {**obs_dict, **sensor.get_observation()}
        for goal in self.goals:
            if goal.add_to_observation_space:
                obs_dict = {**obs_dict, **goal.get_observation()}

        # no normalizing here, that should be handled by the sensors and goals

        return obs_dict

    def step(self, action):
        
        # convert to numpy
        action = np.array(action)
        
        # update world
        self.world.update()

        # apply the action to all robots that have to be moved
        action_offset = 0  # the offset at which the ith robot sits in the action array
        exec_times_cpu = []  # track execution times
        for idx, robot in enumerate(self.robots):
            if not self.active_robots[idx]:
                action_offset += self.action_space_dims[idx]
                continue
            # get the slice of the action vector that belongs to the current robot
            current_robot_action = action[action_offset : self.action_space_dims[idx] + action_offset]
            action_offset += self.action_space_dims[idx]
            exec_time = robot.process_action(current_robot_action)
            if self.use_physics_sim:
                pyb.stepSimulation()
            exec_times_cpu.append(exec_time)

        # update the sensor data
        for sensor in self.sensors:
            sensor.update(self.steps_current_episode)

        # update the collision model
        self.world.perform_collision_check()

        # calculate reward and get termination conditions
        rewards = []
        dones = []
        successes = []
        timeouts = []
        oobs = []
        action_offset = 0
        for idx, goal in enumerate(self.goals):
            # again get the slice of the entire action vector that belongs to the robot/goal in question
            current_robot_action = action[action_offset : self.action_space_dims[idx] + action_offset]
            action_offset += self.action_space_dims[idx]
            # get reward of goal
            reward_info = goal.reward(self.steps_current_episode, current_robot_action)  # tuple: reward, success, done, timeout, out_of_bounds
            rewards.append(reward_info[0])
            successes.append(reward_info[1])
            # set respective robot to inactive after success, if needed
            if reward_info[1] and not goal.continue_after_success:
                self.active_robots[idx] = False
            dones.append(reward_info[2])
            timeouts.append(reward_info[3])
            oobs.append(reward_info[4])

        # determine overall env termination condition
        collision = self.world.collision
        done = np.any(dones) or collision  # one done out of all goals/robots suffices for the entire env to be done or anything collided
        is_success = np.all(successes)  # all goals must be succesful for the entire env to be
        timeout = np.any(timeouts)
        out_of_bounds = np.any(oobs)

        # reward
        # if we are normalizing the reward, we must also account for the number of robots 
        # (each goal will output a reward from -1 to 1, so e.g. three robots would have a cumulative reward range from -3 to 3)
        if self.normalize_rewards:
            self.reward = np.average(rewards)
        # otherwise we can just add the single rewards up
        else:
            self.reward = np.sum(rewards)
        self.reward_cumulative += self.reward

        # update tracking variables and stats
        self.sim_time += self.sim_step
        self.cpu_time = time() - self.cpu_epoch
        self.steps_current_episode += 1
        if done:
            self.success_stat.append(is_success)
            if len(self.success_stat) > self.stat_buffer_size:
                self.success_stat.pop(0)
            self.timeout_stat.append(timeout)
            if len(self.timeout_stat) > self.stat_buffer_size:
                self.timeout_stat.pop(0)
            self.out_of_bounds_stat.append(out_of_bounds)
            if len(self.out_of_bounds_stat) > self.stat_buffer_size:
                self.out_of_bounds_stat.pop(0)
            self.collision_stat.append(collision)
            if len(self.collision_stat) > self.stat_buffer_size:
                self.collision_stat.pop(0)

        if self.logging == 0:
            # no logging
            info = {}
        if self.logging == 1 or self.logging == 2:
            # logging to console or textfile

            # start log dict with env wide information
            info = {"episodes": self.episode,
                    "is_success": is_success, 
                    "collision": collision,
                    "timeout": timeout,
                    "out_of_bounds": out_of_bounds,
                    "step": self.steps_current_episode,
                    "success_rate": np.average(self.success_stat),
                    "out_of_bounds_rate": np.average(self.out_of_bounds_stat),
                    "timeout_rate": np.average(self.timeout_stat),
                    "collision_rate": np.average(self.collision_stat),
                    "sim_time": self.sim_time,
                    "cpu_time": self.cpu_time}
            # get robot execution times
            for idx, robot in enumerate(self.robots):
                if not self.active_robots[idx]:
                    continue
                info["action_cpu_time_" + robot.name] = exec_times_cpu[idx] 
            # get the log data from sensors
            for sensor in self.sensors:
                if sensor.add_to_logging:
                    info = {**info, **sensor.get_data_for_logging()}
            # get log data from goals
            for goal in self.goals:
                if goal.add_to_logging:
                    info = {**info, **goal.get_data_for_logging()}

            self.log.append(info)
            if self.logging == 2:
                print(info)
            # on episode end:
            if done:
                # write to console
                info_string = self._get_info_string(info)
                print(info_string)
                # write to textfile, in this case the entire log so far
                if self.logging == 2:
                    if self.max_episodes == -1 or self.episode == self.max_episodes:
                        pd.DataFrame(self.log).to_csv("./models/env_logs/episode_" + str(self.episode) + ".csv")

        return self._get_obs(), self.reward, done, info

    ###################
    # utility methods #
    ###################

    def _get_info_string(self, info):
        """
        Handles writing info from sensors and goals to console. Also deals with various datatypes and should be updated
        if a new one appears in the code somewhere.
        """
        info_string = ""
        for key in info:
            # handle a few common datatypes and special cases
            if type(info[key]) == np.ndarray:
                to_print = ""
                for ele in info[key]:
                    to_print += str(round(ele, 3)) + " "
                to_print = to_print[:-1]  # cut off the last space
            elif type(info[key]) == np.bool_ or type(info[key]) == bool:
                to_print = str(int(info[key]))
            elif "time" in key and not "timeout" in key:
                if info[key] > 0.001:  # time not very small
                    to_print = str(round(info[key], 3))
                else:  # time very small
                    to_print = "{:.2e}".format(info[key])
            else:
                to_print = str(round(info[key], 3))
            info_string += key + ": " + to_print + ", "
        return info_string[:-1]  # cut off last space

    ####################
    # callback methods #
    ####################

    def set_goal_metric(self, name, value):
        """
        This method is only called from the outside by the custom logging callback (see callbacks/callbacks.py).
        It will change the goal metrics depending on the criteria defined by each goal.
        """
        # find all goals that have a metric with name
        for goal in self.goals:
            if goal.metric_name == name:
                setattr(goal, name, value)  # very bad for performance, but we'll never use so many goals that this will become relevant

    