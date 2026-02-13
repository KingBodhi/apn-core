"""
APN Core Task Runtime - Receives and executes tasks from Pythia via NATS

Subscribes to apn.tasks.{node_id} for task assignments from Pythia.
Executes agents locally with full permissions within the topos/ directory.
Reports results back via apn.tasks.results.

Part of the Sovereign Stack - Layer 0 task execution.
"""

import asyncio
import json
import subprocess
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

try:
    from nats.aio.client import Client as NATS
    NATS_AVAILABLE = True
except ImportError:
    NATS_AVAILABLE = False

from core.logging_config import get_logger

logger = get_logger("task_runtime")

# Agent execution directory (local truth)
TOPOS_DIR = Path.home() / "topos"

# Known agent commands
AGENT_COMMANDS = {
    "nora": "nora",           # LLM assistant
    "editron": "editron",     # Video editing
    "auri": "auri",           # Code generation
    "maci": "maci",           # Image generation
}


class TaskRuntime:
    """Receives tasks via NATS and executes agents locally.

    Agents run with full local permissions within the topos/ directory.
    This is a core principle of the Sovereign Stack - your machine, your rules.
    """

    def __init__(self, nats_url: str, node_id: str, wallet_address: str):
        self.nats_url = nats_url
        self.node_id = node_id
        self.wallet_address = wallet_address
        self.nats: Optional[NATS] = None
        self.running = False
        self._active_tasks: Dict[str, dict] = {}
        self._task_history: list = []
        self._capabilities: list = []

    def _load_capabilities(self):
        """Load available agents from ~/.apn/capabilities.json"""
        try:
            caps_file = Path.home() / ".apn" / "capabilities.json"
            if caps_file.exists():
                with open(caps_file, 'r') as f:
                    caps = json.load(f)
                self._capabilities = caps.get("agents", [])
        except Exception:
            pass

        # Also check which agent binaries are actually available on PATH
        for agent_name, cmd in AGENT_COMMANDS.items():
            if agent_name not in self._capabilities:
                try:
                    result = subprocess.run(
                        ['which', cmd],
                        capture_output=True,
                        timeout=2
                    )
                    if result.returncode == 0:
                        self._capabilities.append(agent_name)
                except Exception:
                    pass

    async def start(self):
        """Start the task runtime service"""
        if not NATS_AVAILABLE:
            logger.error("nats-py not available - install with: pip install nats-py")
            return

        if self.running:
            logger.warning("Task runtime already running")
            return

        self._load_capabilities()

        logger.info(f"Starting task runtime for node {self.node_id}")
        logger.info(f"Available agents: {self._capabilities}")
        logger.info(f"Connecting to NATS: {self.nats_url}")

        try:
            self.nats = NATS()
            await self.nats.connect(self.nats_url)
            logger.info("Connected to NATS relay")

            # Subscribe to tasks directed to this node
            task_subject = f"apn.tasks.{self.node_id}"
            await self.nats.subscribe(task_subject, cb=self._handle_task)
            logger.info(f"Subscribed to {task_subject}")

            # Also subscribe to broadcast tasks (any capable node can pick up)
            await self.nats.subscribe("apn.tasks.broadcast", cb=self._handle_broadcast_task)
            logger.info("Subscribed to apn.tasks.broadcast")

            self.running = True
            logger.info("Task runtime started - ready to execute agents")

        except Exception as e:
            logger.error(f"Failed to start task runtime: {e}")
            raise

    async def stop(self):
        """Stop the task runtime"""
        logger.info("Stopping task runtime...")
        self.running = False

        if self.nats:
            await self.nats.close()
            self.nats = None

        logger.info("Task runtime stopped")

    async def _handle_task(self, msg):
        """Handle a task message directed to this node"""
        try:
            task = json.loads(msg.data.decode())
            task_id = task.get("task_id", "unknown")
            agent = task.get("agent", "")

            logger.info(f"Received task {task_id}: agent='{agent}'")

            # Validate we can execute this agent
            if agent not in self._capabilities and agent not in AGENT_COMMANDS:
                await self._report_result(task_id, False, error=f"Agent '{agent}' not available on this node")
                return

            # Execute the task
            await self._execute_task(task)

        except json.JSONDecodeError:
            logger.error("Invalid task message: not valid JSON")
        except Exception as e:
            logger.error(f"Error handling task: {e}")

    async def _handle_broadcast_task(self, msg):
        """Handle a broadcast task - only execute if we have the capability"""
        try:
            task = json.loads(msg.data.decode())
            agent = task.get("agent", "")

            # Only accept if we can run this agent
            if agent in self._capabilities or agent in AGENT_COMMANDS:
                logger.info(f"Accepting broadcast task for agent '{agent}'")
                await self._execute_task(task)
            else:
                logger.debug(f"Ignoring broadcast task - no capability for '{agent}'")

        except Exception as e:
            logger.error(f"Error handling broadcast task: {e}")

    async def _execute_task(self, task: dict):
        """Execute a task by running the agent locally"""
        task_id = task.get("task_id", "unknown")
        agent = task.get("agent", "")
        params = task.get("params", {})
        input_path = task.get("input")

        start_time = time.time()

        # Track active task
        self._active_tasks[task_id] = {
            "task_id": task_id,
            "agent": agent,
            "status": "executing",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        # Acknowledge receipt
        await self._report_status(task_id, "executing")

        try:
            # Build agent command
            cmd = self._build_agent_command(agent, params, input_path)

            if cmd is None:
                await self._report_result(task_id, False, error=f"Cannot build command for agent '{agent}'")
                return

            logger.info(f"Executing: {' '.join(cmd)}")

            # Execute with full local permissions in topos/ directory
            working_dir = str(TOPOS_DIR) if TOPOS_DIR.exists() else str(Path.home())

            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=working_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "TOPOS_DIR": str(TOPOS_DIR)},
            )

            # Wait for completion with timeout (10 minutes max)
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=600
                )
            except asyncio.TimeoutError:
                process.kill()
                await self._report_result(task_id, False, error="Task timed out (10 min limit)")
                return

            elapsed = time.time() - start_time
            success = process.returncode == 0

            output = stdout.decode('utf-8', errors='replace')[:10000]  # Cap output size
            error_output = stderr.decode('utf-8', errors='replace')[:5000]

            logger.info(
                f"Task {task_id} {'completed' if success else 'failed'} "
                f"in {elapsed:.1f}s (exit code: {process.returncode})"
            )

            await self._report_result(
                task_id,
                success,
                output=output,
                error=error_output if not success else None,
                elapsed_seconds=elapsed,
            )

        except FileNotFoundError:
            await self._report_result(task_id, False, error=f"Agent binary '{agent}' not found")
        except Exception as e:
            await self._report_result(task_id, False, error=str(e))
        finally:
            # Move from active to history
            if task_id in self._active_tasks:
                completed = self._active_tasks.pop(task_id)
                completed["completed_at"] = datetime.now(timezone.utc).isoformat()
                self._task_history.append(completed)
                # Keep only last 100 tasks in history
                if len(self._task_history) > 100:
                    self._task_history = self._task_history[-100:]

    def _build_agent_command(self, agent: str, params: dict, input_path: Optional[str]) -> Optional[list]:
        """Build the command to execute an agent"""
        cmd_name = AGENT_COMMANDS.get(agent, agent)

        # Base command
        cmd = [cmd_name]

        # Add common parameters
        if input_path:
            cmd.extend(["--input", input_path])

        # Agent-specific parameters
        if agent == "nora":
            prompt = params.get("prompt", "")
            if prompt:
                cmd.extend(["--prompt", prompt])
            model = params.get("model", "")
            if model:
                cmd.extend(["--model", model])

        elif agent == "editron":
            action = params.get("action", "edit")
            cmd.extend(["--action", action])
            if params.get("output"):
                cmd.extend(["--output", params["output"]])

        elif agent == "auri":
            task_desc = params.get("task", "")
            if task_desc:
                cmd.extend(["--task", task_desc])
            if params.get("language"):
                cmd.extend(["--language", params["language"]])

        elif agent == "maci":
            prompt = params.get("prompt", "")
            if prompt:
                cmd.extend(["--prompt", prompt])
            if params.get("style"):
                cmd.extend(["--style", params["style"]])

        # Pass any extra params as JSON
        extra = {k: v for k, v in params.items()
                 if k not in ("prompt", "model", "action", "output", "task", "language", "style")}
        if extra:
            cmd.extend(["--params", json.dumps(extra)])

        return cmd

    async def _report_status(self, task_id: str, status: str):
        """Report task status update via NATS"""
        if not self.nats:
            return

        msg = {
            "task_id": task_id,
            "node_id": self.node_id,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        await self.nats.publish(
            "apn.tasks.status",
            json.dumps(msg).encode()
        )

    async def _report_result(
        self,
        task_id: str,
        success: bool,
        output: str = None,
        error: str = None,
        elapsed_seconds: float = None,
    ):
        """Report task result via NATS"""
        if not self.nats:
            return

        result = {
            "task_id": task_id,
            "node_id": self.node_id,
            "wallet_address": self.wallet_address,
            "success": success,
            "output": output,
            "error": error,
            "elapsed_seconds": elapsed_seconds,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        await self.nats.publish(
            "apn.tasks.results",
            json.dumps(result).encode()
        )

        status = "completed" if success else "failed"
        logger.info(f"Reported task {task_id} as {status}")

    def get_active_tasks(self) -> list:
        """Get currently executing tasks"""
        return list(self._active_tasks.values())

    def get_task_history(self, limit: int = 20) -> list:
        """Get recent task execution history"""
        return self._task_history[-limit:]

    def get_stats(self) -> dict:
        """Get runtime statistics"""
        completed = len([t for t in self._task_history if t.get("status") == "completed"])
        failed = len([t for t in self._task_history if t.get("status") == "failed"])
        return {
            "active_tasks": len(self._active_tasks),
            "total_completed": completed,
            "total_failed": failed,
            "capabilities": self._capabilities,
            "running": self.running,
        }


# ─── Global instance ──────────────────────────────────────────────────────

_task_runtime: Optional[TaskRuntime] = None


async def start_task_runtime(nats_url: str, node_id: str, wallet_address: str) -> TaskRuntime:
    """Start the global task runtime service"""
    global _task_runtime

    if _task_runtime and _task_runtime.running:
        logger.warning("Task runtime already running")
        return _task_runtime

    _task_runtime = TaskRuntime(nats_url, node_id, wallet_address)
    await _task_runtime.start()
    return _task_runtime


async def stop_task_runtime():
    """Stop the global task runtime service"""
    global _task_runtime

    if _task_runtime:
        await _task_runtime.stop()
        _task_runtime = None


def get_task_runtime() -> Optional[TaskRuntime]:
    """Get the global task runtime instance"""
    return _task_runtime
