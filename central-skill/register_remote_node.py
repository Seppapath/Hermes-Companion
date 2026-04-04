# central-skill/register_remote_node.py
# Drop this into ~/.hermes/skills/ on your DigitalOcean server

from hermes.skills import Skill

class RegisterRemoteNode(Skill):
    name = "register_remote_node"
    description = "Register a new remote computer into the Hermes network"

    async def execute(self, public_key: str, hostname: str, os_type: str, ip: str = None):
        # Append public key to authorized_keys with restrictions
        authorized_keys_path = "~/.ssh/authorized_keys"
        entry = f'command="echo Restricted access",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty {public_key} # {hostname}'

        # Use terminal tool to append safely
        await self.tools.terminal.run(f'echo "{entry}" >> {authorized_keys_path}')
        await self.tools.terminal.run(f'chmod 600 {authorized_keys_path}')

        # Create machine profile in Hermes
        await self.memory.save(f"Remote node registered: {hostname} ({os_type})")

        return f"Successfully registered {hostname}. Central Hermes can now access it via SSH."
