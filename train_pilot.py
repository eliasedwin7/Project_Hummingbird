#!/usr/bin/env python3
"""Train the AI pilot with PPO (reinforcement learning) on the laptop CPU.

    python train_pilot.py                    # fresh training, 3 million steps
    python train_pilot.py --steps 2000000 --resume   # keep improving the saved pilot

Progress is printed every ~100k steps and logged to models/pilot_log.csv.
The best pilot so far is saved as models/pilot.zip (+ pilot_vecnormalize.pkl),
which the game uses for its AI mode. Runs in the AI environment (see
setup_ai.bat), not the main Python.
"""
import argparse
import csv
import time
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.vec_env import SubprocVecEnv, VecMonitor, VecNormalize

from tello_env import TelloGatesEnv

MODELS = Path(__file__).resolve().parent / "models"


def make_env(rank, seed):
    def _init():
        env = TelloGatesEnv()
        env.reset(seed=seed + rank)
        return env
    return _init


class Progress(BaseCallback):
    """Prints human-readable progress, logs it to CSV and keeps the best pilot."""

    def __init__(self, every, log_path, out, start_best=-1.0):
        super().__init__()
        self.every, self.log_path, self.out = every, log_path, out
        self.next_report, self.best, self.t0 = every, start_best, time.time()
        self.ends = Counter()
        self.gates = []

    def _on_step(self):
        for info, done in zip(self.locals["infos"], self.locals["dones"]):
            if done:
                self.ends[info.get("end", "?")] += 1
                self.gates.append(info.get("gates", 0))
        if self.num_timesteps >= self.next_report and self.gates:
            self.next_report += self.every
            self._report()
        return True

    def _report(self):
        g = np.array(self.gates[-300:])
        mean_r = np.mean([e["r"] for e in self.model.ep_info_buffer]) if self.model.ep_info_buffer else 0
        total = sum(self.ends.values())
        ends = ", ".join(f"{k} {100 * v / total:.0f}%" for k, v in self.ends.most_common(4))
        mins = (time.time() - self.t0) / 60
        print(f"[{mins:5.1f} min] {self.num_timesteps / 1e6:5.2f}M steps | gates per flight: avg {g.mean():5.1f}, "
              f"best {g.max():3d} | reward {mean_r:7.1f} | flights ended by: {ends}", flush=True)
        new_file = not self.log_path.exists()
        with self.log_path.open("a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["minutes", "timesteps", "avg_gates", "best_gates", "mean_reward", "ends"])
            w.writerow([round(mins, 2), self.num_timesteps, round(g.mean(), 2), int(g.max()), round(mean_r, 2), ends])
        if g.mean() > self.best:
            self.best = g.mean()
            self.model.save(self.out)
            self.model.get_env().save(str(self.out) + "_vecnormalize.pkl")
            print(f"           -> new best pilot saved ({self.best:.1f} gates per flight)", flush=True)
        self.ends.clear()
        self.gates = self.gates[-300:]


def main():
    ap = argparse.ArgumentParser(description="Train the Tello AI pilot")
    ap.add_argument("--steps", type=int, default=3_000_000, help="decisions to train for (10 per simulated second)")
    ap.add_argument("--envs", type=int, default=6, help="simulators running in parallel")
    ap.add_argument("--resume", action="store_true", help="continue from models/pilot.zip")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    torch.set_num_threads(2)  # leave the other cores to the simulators
    MODELS.mkdir(exist_ok=True)
    out = MODELS / "pilot"
    env = VecMonitor(SubprocVecEnv([make_env(i, args.seed * 100) for i in range(args.envs)]), info_keywords=("gates",))

    if args.resume and out.with_suffix(".zip").exists():
        env = VecNormalize.load(str(out) + "_vecnormalize.pkl", env)
        env.training = True
        model = PPO.load(out, env=env, device="cpu")
        print(f"Resuming from {out}.zip")
    else:
        env = VecNormalize(env, norm_obs=True, norm_reward=True, clip_obs=10.0, gamma=0.99)
        model = PPO("MlpPolicy", env, n_steps=1024, batch_size=1024, n_epochs=10, learning_rate=3e-4,
                    gamma=0.99, gae_lambda=0.95, clip_range=0.2, ent_coef=0.0, seed=args.seed, device="cpu",
                    policy_kwargs=dict(net_arch=dict(pi=[128, 128], vf=[128, 128])))

    print(f"Training for {args.steps / 1e6:.1f}M steps on {args.envs} simulators "
          f"(~{args.steps / 10 / 3600:.0f} hours of simulated flying)...", flush=True)
    callback = Progress(every=100_000, log_path=MODELS / "pilot_log.csv", out=out)
    try:
        model.learn(total_timesteps=args.steps, callback=callback, reset_num_timesteps=not args.resume)
    except KeyboardInterrupt:
        print("Stopped early.")
    model.save(MODELS / "pilot_last")
    env.save(str(MODELS / "pilot_last") + "_vecnormalize.pkl")
    print(f"Done. Best pilot: {out}.zip ({callback.best:.1f} gates per flight). Latest: models/pilot_last.zip")
    env.close()


if __name__ == "__main__":
    main()
