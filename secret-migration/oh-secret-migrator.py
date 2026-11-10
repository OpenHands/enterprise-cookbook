#!/usr/bin/env python3
"""
OpenHands Secret Migration Tool

Securely migrate secrets between OpenHands instances.
Secret values are kept in memory only and never persisted to disk unencrypted.
"""

import argparse
import getpass
import json
import sys
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import urllib.request
import urllib.error
import urllib.parse


@dataclass
class Secret:
    """Represents a secret with its metadata."""
    name: str
    description: Optional[str]
    scope: str  # 'personal' or 'organization'
    system_managed: bool = False
    value: Optional[str] = None  # Only populated when user provides it


class OpenHandsClient:
    """Client for OpenHands API."""
    
    def __init__(self, base_url: str, api_key: str, service_key: Optional[str] = None):
        """Initialize client with base URL and API key.
        
        Args:
            base_url: Base URL of the OpenHands instance
            api_key: User API key for authentication
            service_key: Optional service API key for retrieving secret values
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.service_key = service_key
        self.org_id = None  # Will be fetched if needed
        self.user_id = None  # Will be fetched if needed
    
    def _make_request(
        self,
        method: str,
        path: str,
        data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        expect_json: bool = True
    ) -> Tuple[Optional[Dict], Optional[str]]:
        """Make an HTTP request to the API.
        
        Args:
            method: HTTP method
            path: API path
            data: Optional request body data
            headers: Optional additional headers
            expect_json: If True, parse response as JSON; if False, return as plain text
        
        Returns:
            Tuple of (response_data, error_message)
        """
        url = f"{self.base_url}{path}"
        req_headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        if headers:
            req_headers.update(headers)
        
        request_body = json.dumps(data).encode('utf-8') if data else None
        
        try:
            req = urllib.request.Request(
                url,
                data=request_body,
                headers=req_headers,
                method=method
            )
            
            with urllib.request.urlopen(req) as response:
                if response.status in (200, 201):
                    response_data = response.read().decode('utf-8')
                    if not response_data:
                        return {} if expect_json else '', None
                    
                    if expect_json:
                        return json.loads(response_data), None
                    else:
                        return response_data, None
                else:
                    return None, f"HTTP {response.status}"
        
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='ignore')
            try:
                error_data = json.loads(error_body)
                error_msg = error_data.get('detail', error_body)
            except:
                error_msg = error_body
            return None, f"HTTP {e.code}: {error_msg}"
        
        except Exception as e:
            return None, f"Request failed: {str(e)}"
    
    def get_current_user(self) -> Tuple[Optional[Dict], Optional[str]]:
        """Get current user information."""
        data, error = self._make_request('GET', '/api/user/me')
        if data and not error:
            self.user_id = data.get('id')
        return data, error
    
    def list_personal_secrets(self) -> Tuple[List[Secret], Optional[str]]:
        """List all personal secrets (names only, no values)."""
        secrets = []
        page_id = None
        
        while True:
            path = '/api/v1/secrets/search?limit=100'
            if page_id:
                path += f'&page_id={page_id}'
            
            data, error = self._make_request('GET', path)
            if error:
                return [], error
            
            for item in data.get('items', []):
                secrets.append(Secret(
                    name=item['name'],
                    description=item.get('description'),
                    scope=item.get('scope', 'personal'),
                    system_managed=item.get('system_managed', False)
                ))
            
            page_id = data.get('next_page_id')
            if not page_id:
                break
        
        return secrets, None
    
    def list_org_secrets(self, org_id: str) -> Tuple[List[Secret], Optional[str]]:
        """List all organization secrets (names only, no values)."""
        secrets = []
        path = f'/api/organizations/{org_id}/secrets'
        
        data, error = self._make_request('GET', path)
        if error:
            return [], error
        
        for item in data.get('items', []):
            secrets.append(Secret(
                name=item['name'],
                description=item.get('description'),
                scope='organization',
                system_managed=item.get('system_managed', False)
            ))
        
        return secrets, None
    
    def get_user_orgs(self) -> Tuple[List[Dict], Optional[str]]:
        """Get list of organizations for the current user."""
        data, error = self._make_request('GET', '/api/organizations')
        if error:
            return [], error
        return data.get('items', []), None
    
    def create_personal_secret(self, secret: Secret) -> Optional[str]:
        """Create a personal secret."""
        data = {
            'name': secret.name,
            'value': secret.value,
        }
        if secret.description:
            data['description'] = secret.description
        
        _, error = self._make_request('POST', '/api/v1/secrets', data=data)
        return error
    
    def create_org_secret(self, org_id: str, secret: Secret) -> Optional[str]:
        """Create an organization secret."""
        data = {
            'name': secret.name,
            'value': secret.value,
        }
        if secret.description:
            data['description'] = secret.description
        
        path = f'/api/organizations/{org_id}/secrets'
        _, error = self._make_request('POST', path, data=data)
        return error
    
    def retrieve_secret_values(self, secrets: List[Secret], org_id: Optional[str] = None) -> Tuple[List[Secret], Optional[str]]:
        """Retrieve secret values using service API key.
        
        This requires a service API key and uses the secret-lookups flow:
        1. POST /api/service/users/{user_id}/orgs/{org_id}/secret-lookups to get JWS tokens
        2. GET /api/v1/webhooks/secrets with each JWS token to get the actual value
        
        Args:
            secrets: List of secrets to retrieve values for
            org_id: Organization ID (required if service_key is provided)
            
        Returns:
            Tuple of (secrets_with_values, error_message)
        """
        if not self.service_key:
            return [], "Service API key is required to retrieve secret values"
        
        if not org_id:
            return [], "Organization ID is required for service key authentication"
        
        if not self.user_id:
            return [], "User ID not available - call get_current_user() first"
        
        # Step 1: Get JWS tokens for each secret
        secret_names = [s.name for s in secrets]
        path = f'/api/service/users/{self.user_id}/orgs/{org_id}/secret-lookups'
        
        headers = {'X-Service-API-Key': self.service_key}
        data = {
            'names': secret_names,
            'ttl_seconds': 300  # 5 minutes should be enough
        }
        
        response, error = self._make_request('POST', path, data=data, headers=headers)
        if error:
            return [], f"Failed to get secret lookup tokens: {error}"
        
        lookups = response.get('lookups', {})
        missing = response.get('missing', [])
        
        # Step 2: Retrieve actual values using JWS tokens
        secrets_with_values = []
        
        for secret in secrets:
            if secret.name in missing:
                print(f"  ⚠️  Secret {secret.name} not found or inaccessible")
                continue
            
            jws_token = lookups.get(secret.name)
            if not jws_token:
                print(f"  ⚠️  No lookup token for {secret.name}")
                continue
            
            # GET /api/v1/webhooks/secrets with the JWS token
            # This endpoint returns plain text, not JSON
            value_response, value_error = self._make_request(
                'GET',
                '/api/v1/webhooks/secrets',
                headers={'X-Access-Token': jws_token},
                expect_json=False
            )
            
            if value_error:
                print(f"  ⚠️  Failed to retrieve value for {secret.name}: {value_error}")
                continue
            
            secret.value = value_response
            secrets_with_values.append(secret)
            masked = mask_value(secret.value)
            print(f"  ✓ Retrieved value for {secret.name}: {masked}")
        
        return secrets_with_values, None


def mask_value(value: str, show_chars: int = 4) -> str:
    """Mask a secret value showing only first few characters."""
    if not value:
        return '(empty)'
    if len(value) <= show_chars:
        return '*' * len(value)
    return value[:show_chars] + '*' * 8


def select_secrets(secrets: List[Secret]) -> List[Secret]:
    """Interactive selection of secrets to migrate."""
    if not secrets:
        print("No secrets found to migrate.")
        return []
    
    print("\n" + "="*70)
    print("Available Secrets")
    print("="*70)
    
    # Group by scope
    personal = [s for s in secrets if s.scope == 'personal' and not s.system_managed]
    org = [s for s in secrets if s.scope == 'organization']
    system = [s for s in secrets if s.system_managed]
    
    if personal:
        print(f"\nPersonal Secrets ({len(personal)}):")
        for i, secret in enumerate(personal, 1):
            desc = f" - {secret.description}" if secret.description else ""
            print(f"  {i}. {secret.name}{desc}")
    
    if org:
        print(f"\nOrganization Secrets ({len(org)}):")
        for i, secret in enumerate(org, len(personal) + 1):
            desc = f" - {secret.description}" if secret.description else ""
            print(f"  {i}. {secret.name}{desc}")
    
    if system:
        print(f"\nSystem-Managed Secrets ({len(system)}) - Cannot be migrated:")
        for secret in system:
            print(f"  - {secret.name}")
    
    # Filter out system-managed secrets
    selectable = personal + org
    
    if not selectable:
        print("\nNo secrets available for migration.")
        return []
    
    print("\n" + "="*70)
    print("\nEnter the numbers of secrets to migrate (comma-separated),")
    print("or 'all' to migrate all non-system secrets, or 'none' to cancel:")
    
    while True:
        choice = input("> ").strip().lower()
        
        if choice == 'none':
            return []
        
        if choice == 'all':
            return selectable
        
        try:
            indices = [int(x.strip()) for x in choice.split(',')]
            selected = []
            for idx in indices:
                if 1 <= idx <= len(selectable):
                    selected.append(selectable[idx - 1])
                else:
                    print(f"Invalid selection: {idx}. Please try again.")
                    break
            else:
                return selected
        except ValueError:
            print("Invalid input. Please enter numbers separated by commas, 'all', or 'none'.")


def collect_secret_values(secrets: List[Secret], values_file: Optional[str] = None, from_env: bool = False) -> List[Secret]:
    """Prompt user to enter values for selected secrets.
    
    Args:
        secrets: List of secrets that need values
        values_file: Optional path to JSON file containing secret values
        from_env: If True, read values from environment variables
    """
    if not secrets:
        return []
    
    # Read values from environment if requested
    import os
    env_values = {}
    if from_env:
        for secret in secrets:
            env_value = os.environ.get(secret.name)
            if env_value:
                env_values[secret.name] = env_value
        
        if env_values:
            print(f"\n✓ Found {len(env_values)} secret value(s) in environment variables")
    
    # Load values from file if provided
    file_values = {}
    if values_file:
        try:
            with open(values_file, 'r') as f:
                file_values = json.load(f)
            print(f"\n✓ Loaded {len(file_values)} secret value(s) from {values_file}")
        except FileNotFoundError:
            print(f"\n⚠️  Values file not found: {values_file}")
        except json.JSONDecodeError as e:
            print(f"\n⚠️  Invalid JSON in values file: {e}")
        except Exception as e:
            print(f"\n⚠️  Error reading values file: {e}")
    
    print("\n" + "="*70)
    print("Collecting Secret Values")
    print("="*70)
    
    if not from_env and not file_values:
        print("\nFor each secret, please enter its value.")
        print("Values are hidden while typing for security.")
        print("Tip: Use --from-env inside a sandbox or --values-file for bulk import.")
    
    print("Press Ctrl+C at any time to cancel.\n")
    
    secrets_with_values = []
    
    try:
        for secret in secrets:
            desc = f" ({secret.description})" if secret.description else ""
            scope_tag = f"[{secret.scope}]"
            
            # Priority 1: Environment variable
            if secret.name in env_values:
                secret.value = env_values[secret.name]
                secrets_with_values.append(secret)
                masked = mask_value(secret.value)
                print(f"{scope_tag} {secret.name}{desc}: {masked} (from environment)")
                continue
            
            # Priority 2: Values file
            if secret.name in file_values:
                secret.value = file_values[secret.name]
                secrets_with_values.append(secret)
                masked = mask_value(secret.value)
                print(f"{scope_tag} {secret.name}{desc}: {masked} (from file)")
                continue
            
            while True:
                value = getpass.getpass(f"{scope_tag} {secret.name}{desc}: ")
                
                if not value:
                    print("  ⚠️  Value cannot be empty. Try again or press Ctrl+C to cancel.")
                    continue
                
                # Confirm value
                confirm = getpass.getpass(f"  Confirm value for {secret.name}: ")
                
                if value == confirm:
                    secret.value = value
                    secrets_with_values.append(secret)
                    print(f"  ✓ Value captured for {secret.name}\n")
                    break
                else:
                    print("  ⚠️  Values don't match. Try again.\n")
        
        return secrets_with_values
    
    except KeyboardInterrupt:
        print("\n\nCancelled by user.")
        return []


def migrate_secrets(
    source_url: str,
    source_key: str,
    dest_url: str,
    dest_key: str,
    include_org: bool = False,
    dry_run: bool = False,
    values_file: Optional[str] = None,
    source_service_key: Optional[str] = None,
    from_env: bool = False
) -> int:
    """Main migration logic."""
    
    mode = "DRY RUN - Preview Only" if dry_run else "Live Migration"
    print("="*70)
    print(f"OpenHands Secret Migration Tool - {mode}")
    print("="*70)
    
    # Initialize clients
    step_total = 5 if dry_run else 6
    
    print(f"\n[1/{step_total}] Connecting to source instance...")
    source_client = OpenHandsClient(source_url, source_key, source_service_key)
    
    user_data, error = source_client.get_current_user()
    if error:
        print(f"❌ Failed to connect to source: {error}")
        return 1
    
    print(f"✓ Connected to source as: {user_data.get('email', 'unknown')}")
    
    if not dry_run:
        print(f"\n[2/{step_total}] Connecting to destination instance...")
        dest_client = OpenHandsClient(dest_url, dest_key)
        
        user_data, error = dest_client.get_current_user()
        if error:
            print(f"❌ Failed to connect to destination: {error}")
            return 1
        
        print(f"✓ Connected to destination as: {user_data.get('email', 'unknown')}")
    else:
        dest_client = None
    
    # List secrets from source
    step_num = 2 if dry_run else 3
    print(f"\n[{step_num}/{step_total}] Fetching secrets from source...")
    
    personal_secrets, error = source_client.list_personal_secrets()
    if error:
        print(f"❌ Failed to list personal secrets: {error}")
        return 1
    
    print(f"✓ Found {len(personal_secrets)} personal secret(s)")
    
    org_secrets = []
    source_org_id = None
    
    if include_org:
        orgs, error = source_client.get_user_orgs()
        if error:
            print(f"⚠️  Failed to list organizations: {error}")
        elif not orgs:
            print("⚠️  No organizations found")
        else:
            # Use first org for now (could be enhanced to let user choose)
            source_org_id = orgs[0]['id']
            org_name = orgs[0].get('name', source_org_id)
            print(f"ℹ️  Using organization: {org_name}")
            
            org_secrets, error = source_client.list_org_secrets(source_org_id)
            if error:
                print(f"⚠️  Failed to list org secrets: {error}")
            else:
                print(f"✓ Found {len(org_secrets)} organization secret(s)")
    
    all_secrets = personal_secrets + org_secrets
    
    if not all_secrets:
        print("\n✓ No secrets to migrate.")
        return 0
    
    # Let user select secrets
    step_num += 1
    print(f"\n[{step_num}/{step_total}] Select secrets to migrate...")
    selected_secrets = select_secrets(all_secrets)
    
    if not selected_secrets:
        print("\n✓ No secrets selected. Exiting.")
        return 0
    
    print(f"\n✓ Selected {len(selected_secrets)} secret(s) to migrate")
    
    if dry_run:
        # In dry-run mode, just show what would be migrated
        print(f"\n[{step_num + 1}/{step_total}] Preview of secrets to migrate...")
        print("\n" + "="*70)
        print("Secrets that would be migrated:")
        print("="*70)
        
        personal = [s for s in selected_secrets if s.scope == 'personal']
        org = [s for s in selected_secrets if s.scope == 'organization']
        
        if personal:
            print(f"\nPersonal Secrets ({len(personal)}):")
            for secret in personal:
                desc = f" - {secret.description}" if secret.description else ""
                print(f"  • {secret.name}{desc}")
        
        if org:
            print(f"\nOrganization Secrets ({len(org)}):")
            for secret in org:
                desc = f" - {secret.description}" if secret.description else ""
                print(f"  • {secret.name}{desc}")
        
        print("\n" + "="*70)
        print("ℹ️  This was a dry run. No secrets were migrated.")
        print("   Run without --dry-run to perform actual migration.")
        print("="*70)
        return 0
    
    # Collect values
    step_num += 1
    print(f"\n[{step_num}/{step_total}] Collect secret values...")
    
    # If we have a service key, try to retrieve values automatically
    if source_service_key and source_org_id:
        print("ℹ️  Using service key to retrieve secret values from source...")
        secrets_with_values, error = source_client.retrieve_secret_values(selected_secrets, source_org_id)
        
        if error:
            print(f"⚠️  Service key retrieval failed: {error}")
            print("   Falling back to other methods...")
            secrets_with_values = collect_secret_values(selected_secrets, values_file, from_env)
        else:
            print(f"✓ Retrieved {len(secrets_with_values)} secret value(s) via service key")
            
            # Still check values file or prompt for any secrets that weren't retrieved
            missing = [s for s in selected_secrets if s.value is None]
            if missing:
                print(f"ℹ️  {len(missing)} secret(s) need additional collection...")
                additional = collect_secret_values(missing, values_file, from_env)
                secrets_with_values.extend(additional)
    else:
        secrets_with_values = collect_secret_values(selected_secrets, values_file, from_env)
    
    if not secrets_with_values:
        print("\n✓ No secrets to migrate. Exiting.")
        return 0
    
    # Migrate to destination
    step_num += 1
    print(f"\n[{step_num}/{step_total}] Migrating secrets to destination...")
    
    # Handle org secrets
    dest_org_id = None
    org_secrets_to_create = [s for s in secrets_with_values if s.scope == 'organization']
    
    if org_secrets_to_create:
        if not include_org:
            print("⚠️  Organization secrets were selected but --include-org was not specified.")
            print("   Skipping organization secrets.")
            secrets_with_values = [s for s in secrets_with_values if s.scope != 'organization']
        else:
            orgs, error = dest_client.get_user_orgs()
            if error or not orgs:
                print(f"⚠️  Cannot create org secrets: {error or 'No orgs found'}")
                print("   Skipping organization secrets.")
                secrets_with_values = [s for s in secrets_with_values if s.scope != 'organization']
            else:
                dest_org_id = orgs[0]['id']
                dest_org_name = orgs[0].get('name', dest_org_id)
                print(f"ℹ️  Target organization: {dest_org_name}")
    
    # Create secrets
    success_count = 0
    error_count = 0
    
    for secret in secrets_with_values:
        scope_tag = f"[{secret.scope}]"
        
        if secret.scope == 'organization':
            if not dest_org_id:
                print(f"⚠️  {scope_tag} {secret.name} - SKIPPED (no org context)")
                error_count += 1
                continue
            
            error = dest_client.create_org_secret(dest_org_id, secret)
        else:
            error = dest_client.create_personal_secret(secret)
        
        if error:
            print(f"❌ {scope_tag} {secret.name} - FAILED: {error}")
            error_count += 1
        else:
            print(f"✓ {scope_tag} {secret.name} - migrated successfully")
            success_count += 1
        
        # Clear the value from memory
        secret.value = None
    
    # Summary
    print("\n" + "="*70)
    print("Migration Summary")
    print("="*70)
    print(f"✓ Successfully migrated: {success_count}")
    if error_count > 0:
        print(f"❌ Failed: {error_count}")
    print("="*70)
    
    return 0 if error_count == 0 else 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Migrate secrets between OpenHands instances',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Migrate personal secrets only
  %(prog)s --source-url https://source.example.com \\
           --dest-url https://dest.example.com
  
  # Include organization secrets
  %(prog)s --source-url https://source.example.com \\
           --dest-url https://dest.example.com \\
           --include-org

Notes:
  - API keys can be provided via command line or environment variables
  - Secret values are prompted interactively and never stored on disk
  - System-managed secrets (like OPENHANDS_API_KEY) cannot be migrated
        """
    )
    
    parser.add_argument(
        '--source-url',
        required=True,
        help='Source OpenHands instance URL'
    )
    
    parser.add_argument(
        '--source-key',
        help='Source API key (or set SOURCE_API_KEY env var)'
    )
    
    parser.add_argument(
        '--source-service-key',
        help='Source service API key for automatic secret retrieval (or set SOURCE_SERVICE_KEY env var)'
    )
    
    parser.add_argument(
        '--dest-url',
        required=True,
        help='Destination OpenHands instance URL'
    )
    
    parser.add_argument(
        '--dest-key',
        help='Destination API key (or set DEST_API_KEY env var)'
    )
    
    parser.add_argument(
        '--include-org',
        action='store_true',
        help='Include organization secrets in migration'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview secrets without migrating (no values collected)'
    )
    
    parser.add_argument(
        '--values-file',
        help='Path to JSON file containing secret values (format: {"SECRET_NAME": "value"})'
    )
    
    parser.add_argument(
        '--from-env',
        action='store_true',
        help='Read secret values from environment variables (use when running inside a sandbox)'
    )
    
    args = parser.parse_args()
    
    # Get API keys from args or env
    import os
    source_key = args.source_key or os.environ.get('SOURCE_API_KEY')
    source_service_key = args.source_service_key or os.environ.get('SOURCE_SERVICE_KEY')
    dest_key = args.dest_key or os.environ.get('DEST_API_KEY')
    
    if not source_key:
        source_key = getpass.getpass("Enter source API key: ")
    
    # Optionally prompt for service key if not provided
    if not source_service_key and not args.values_file:
        prompt_service = input("\nDo you have a source service API key for automatic secret retrieval? (y/N): ")
        if prompt_service.lower() in ('y', 'yes'):
            source_service_key = getpass.getpass("Enter source service API key: ")
    
    # Destination key not required for dry-run
    if not args.dry_run:
        if not dest_key:
            dest_key = getpass.getpass("Enter destination API key: ")
    else:
        dest_key = dest_key or "dry-run-placeholder"
    
    if not source_key:
        print("Error: Source API key is required")
        return 1
    
    if not args.dry_run and not dest_key:
        print("Error: Destination API key is required")
        return 1
    
    try:
        return migrate_secrets(
            args.source_url,
            source_key,
            args.dest_url,
            dest_key,
            args.include_org,
            args.dry_run,
            args.values_file,
            source_service_key,
            args.from_env
        )
    except KeyboardInterrupt:
        print("\n\nMigration cancelled by user.")
        return 130
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
