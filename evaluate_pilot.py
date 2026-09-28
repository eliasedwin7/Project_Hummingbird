#!/usr/bin/env python3
"""Exam for the AI pilot: the same 30 Endless courses (fixed seeds) plus every
fixed course, flown by the trained AI and by the hand-written scripted pilot.

    python evaluate_pilot.py                 # models/pilot.zip
    python evaluate_pilot.py --model models/pilot_last --noisy

--noisy keeps training-style randomisation on (wind, delay, different drone
response), a rough test of whether the pilot would cope with a real Tello.
"""
import argparse
import math
import pickle
from collections import Counter

import numpy as np
from stable_baselines3 import PPO

from tello_env import FIXED_COURSES, TelloGatesEnv, _gate_after

EXAM_SEEDS = range(9000, 9030)


def scripted(env):
    """Hand-written pilot: aim at the gate, match its height. Knows nothing about pillars."""
    d, g = env.race.drone, _gate_after(env.race, 0)
    (nx, ny), (cx, cy, cz) = g.normal, g.center
    along = (d.x - cx) * nx + (d.y - cy) * ny
    tx, ty = (cx + nx * .8, cy + ny * .8) if along > -1.5 else (cx - nx * 1.2, cy - ny * 1.2)
    err = (math.degrees(math.atan2(tx - d.x, ty - d.y)) - d.yaw + 180) % 360 - 180
    fwd = max(0, min(100, math.hypot(tx - d.x, ty - d.y) * 30)) * (1 if abs(err) < 30 else .2)
    return np.array([0, fwd, (cz - d.z) * 150, err * 3], np.float32).clip(-100, 100) / 100


def ai_policy(path):
    model = PPO.load(path, device="cpu")
    with open(f"{path}_vecnormalize.pkl", "rb") as f:
        norm = pickle.load(f)
    mean, std = norm.obs_rms.mean, np.sqrt(norm.obs_rms.var + norm.epsilon)

    def act(env, obs):
        return model.predict(np.clip((obs - mean) / std, -norm.clip_obs, norm.clip_obs), deterministic=True)[0]
    return act


def fly(env, policy, course, seed):
    obs, _ = env.reset(seed=seed, options={"course": course, "course_seed": seed})
    done = False
    while not done:
        obs, _, term, trunc, info = env.step(policy(env, obs))
        done = term or trunc
    return info["gates"], info["end"], env.race.time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/pilot")
    ap.add_argument("--noisy", action="store_true", help="keep wind / delay / drone variation on")
    args = ap.parse_args()
    env = TelloGatesEnv(randomize=args.noisy, max_seconds=180)
    pilots = {"AI pilot": ai_policy(args.model), "scripted pilot": lambda env, obs: scripted(env)}

    print(f"Endless exam: {len(EXAM_SEEDS)} courses, 3 minutes max each{' (with wind/delay/noise)' if args.noisy else ''}")
    for name, policy in pilots.items():
        results = [fly(env, policy, "endless", s) for s in EXAM_SEEDS]
        gates = np.array([r[0] for r in results])
        ends = Counter(r[1] for r in results)
        print(f"  {name:15s} gates avg {gates.mean():5.1f}  median {np.median(gates):5.1f}  best {gates.max():3d} | "
              f"ended by: {', '.join(f'{k} {v}' for k, v in ends.most_common())}")

    print("Fixed courses (time to finish):")
    for course in FIXED_COURSES:
        row = []
        for name, policy in pilots.items():
            gates, end, t = fly(env, policy, course, 1)
            row.append(f"{name}: {f'{t:5.1f} s' if end == 'finished' else f'did not finish ({end}, {gates} gates)'}")
        print(f"  {course:14s} " + " | ".join(row))


if __name__ == "__main__":
    main()
