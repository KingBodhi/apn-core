#!/usr/bin/env python3
"""
APN Core - Wallet Recovery Tool

Helps recover and consolidate VIBE rewards from duplicate wallets
caused by identity regeneration issues.
"""
import sys
import json
from pathlib import Path


def main():
    """Main recovery tool"""
    print("=" * 60)
    print("APN CORE - WALLET RECOVERY TOOL")
    print("=" * 60)
    print()

    # Check for identity file
    config_dir = Path.home() / ".apn"
    identity_file = config_dir / "node_identity.json"
    backup_file = config_dir / "node_identity.json.backup"

    print("1. Checking for identity files...")
    print()

    if identity_file.exists():
        print(f"✓ Current identity: {identity_file}")
        try:
            with open(identity_file) as f:
                data = json.load(f)
            print(f"  Node ID: {data.get('node_id', 'MISSING')}")
            print(f"  Wallet:  {data.get('payment_address', 'MISSING')}")
            print(f"  Created: {data.get('created_at', 'Unknown')}")
        except Exception as e:
            print(f"  ✗ Error reading file: {e}")
    else:
        print(f"✗ No current identity found at: {identity_file}")

    print()

    if backup_file.exists():
        print(f"✓ Backup identity: {backup_file}")
        try:
            with open(backup_file) as f:
                data = json.load(f)
            print(f"  Node ID: {data.get('node_id', 'MISSING')}")
            print(f"  Wallet:  {data.get('payment_address', 'MISSING')}")
            print(f"  Created: {data.get('created_at', 'Unknown')}")
        except Exception as e:
            print(f"  ✗ Error reading file: {e}")
    else:
        print(f"✗ No backup identity found")

    print()
    print("=" * 60)
    print("RECOVERY OPTIONS")
    print("=" * 60)
    print()

    if backup_file.exists() and identity_file.exists():
        print("Option 1: Restore from backup")
        print("  This will replace current identity with backup")
        print()
        print("  Commands:")
        print(f"    cp {backup_file} {identity_file}")
        print(f"    chmod 600 {identity_file}")
        print()

    print("Option 2: View all node identities on this system")
    print("  Check other locations where identity might be saved:")
    print()
    print("  Commands:")
    print("    find ~ -name 'node_identity.json' 2>/dev/null")
    print()

    print("Option 3: Contact network admin")
    print("  To consolidate VIBE from multiple wallets to one:")
    print()
    print("  1. Identify all your wallet addresses (from database or logs)")
    print("  2. Choose which wallet to keep (current one)")
    print("  3. Request admin to merge VIBE from old wallets to current")
    print()

    print("=" * 60)
    print("PREVENTION")
    print("=" * 60)
    print()
    print("To prevent this issue in the future:")
    print()
    print("1. Backup your identity regularly:")
    print(f"   cp {identity_file} ~/apn_identity_backup.json")
    print()
    print("2. Ensure proper permissions:")
    print(f"   chmod 700 {config_dir}")
    print(f"   chmod 600 {identity_file}")
    print()
    print("3. Check identity before starting:")
    print(f"   cat {identity_file}")
    print()
    print("4. Monitor for new node IDs in network")
    print()

    print("=" * 60)


if __name__ == "__main__":
    main()
