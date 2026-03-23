"""
src/train/rollout_buffer.py

PPO Rollout Buffer
收集一轮完整的交互数据用于 PPO 更新
"""

from typing import Dict, List, Optional, Tuple
import numpy as np
import torch


class RolloutBuffer:
    """
    存储 N 步交互数据的缓冲区

    收集:
    - 观测 (obs)
    - 动作 (actions)
    - 旧 log 概率 (old_log_probs)
    - 奖励 (rewards)
    - 是否结束 (dones)
    - 价值估计 (values)
    - 动作 mask (action_masks)
    """

    def __init__(self, buffer_size: int = 2048):
        self.buffer_size = buffer_size
        self.reset()

    def reset(self):
        self.obs_list: List[Dict] = []
        self.actions: List[int] = []
        self.old_log_probs: List[float] = []
        self.rewards: List[float] = []
        self.dones: List[bool] = []
        self.values: List[float] = []
        self.action_masks: List[Optional[List[bool]]] = []
        self._size = 0

    def add(
        self,
        obs: Dict,
        action: int,
        log_prob: float,
        reward: float,
        done: bool,
        value: float,
        action_mask: Optional[List[bool]] = None,
    ):
        self.obs_list.append(obs)
        self.actions.append(action)
        self.old_log_probs.append(log_prob)
        self.rewards.append(reward)
        self.dones.append(done)
        self.values.append(value)
        self.action_masks.append(action_mask)
        self._size += 1

    def is_full(self) -> bool:
        return self._size >= self.buffer_size

    def __len__(self) -> int:
        return self._size

    def get_tensors(
        self, device: str
    ) -> Tuple[Dict[str, torch.Tensor], torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, Optional[torch.Tensor]]:
        """
        将缓冲区数据转为 Tensor

        Returns:
            obs_tensors, actions, old_log_probs, advantages, returns, action_masks
        """
        N = self._size

        # Stack obs dict
        obs_tensors = {}
        for key in self.obs_list[0].keys():
            arrays = [self.obs_list[i][key] for i in range(N)]
            obs_tensors[key] = torch.tensor(
                np.stack(arrays), dtype=torch.float32
            ).to(device)
            if key == "screen_type":
                obs_tensors[key] = obs_tensors[key].long()

        actions = torch.tensor(self.actions, dtype=torch.long).to(device)
        old_log_probs = torch.tensor(self.old_log_probs, dtype=torch.float32).to(device)

        # GAE 在外部计算后设置
        advantages = torch.tensor(
            getattr(self, "_advantages", [0.0] * N), dtype=torch.float32
        ).to(device)
        returns = torch.tensor(
            getattr(self, "_returns", [0.0] * N), dtype=torch.float32
        ).to(device)

        # Action masks
        masks_tensor = None
        if self.action_masks[0] is not None:
            masks_arr = np.array(self.action_masks[:N], dtype=np.float32)
            masks_tensor = torch.tensor(masks_arr, dtype=torch.bool).to(device)

        return obs_tensors, actions, old_log_probs, advantages, returns, masks_tensor

    def set_gae_results(self, advantages: List[float], returns: List[float]):
        """设置 GAE 计算结果"""
        self._advantages = advantages
        self._returns = returns