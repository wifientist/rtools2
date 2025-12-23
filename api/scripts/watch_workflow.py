#!/usr/bin/env python3
"""
Workflow Progress Viewer

Terminal-based real-time monitoring for workflow jobs.

Usage:
    python watch_workflow.py <job_id> [--api-url http://localhost:8000]

Example:
    python watch_workflow.py abc-123-def-456
"""

import sys
import time
import requests
import argparse
from datetime import datetime
from typing import Dict, Any, Optional


class ProgressViewer:
    """Terminal progress viewer for workflow jobs"""

    # ANSI color codes
    COLORS = {
        'header': '\033[95m',
        'blue': '\033[94m',
        'cyan': '\033[96m',
        'green': '\033[92m',
        'yellow': '\033[93m',
        'red': '\033[91m',
        'gray': '\033[90m',
        'bold': '\033[1m',
        'underline': '\033[4m',
        'end': '\033[0m'
    }

    # Status icons
    ICONS = {
        'COMPLETED': '✅',
        'RUNNING': '🔄',
        'PENDING': '⏸️',
        'FAILED': '❌',
        'SKIPPED': '⏭️',
        'PARTIAL': '⚠️'
    }

    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip('/')
        self.last_status = None

    def clear_screen(self):
        """Clear terminal screen"""
        print("\033[2J\033[H", end='')

    def color(self, text: str, color: str) -> str:
        """Apply color to text"""
        return f"{self.COLORS.get(color, '')}{text}{self.COLORS['end']}"

    def format_duration(self, seconds: Optional[float]) -> str:
        """Format duration in human-readable form"""
        if seconds is None:
            return "N/A"

        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"

    def draw_progress_bar(self, percent: float, width: int = 50) -> str:
        """Draw ASCII progress bar"""
        filled = int(width * percent / 100)
        empty = width - filled
        bar = '█' * filled + '░' * empty

        if percent >= 100:
            color = 'green'
        elif percent >= 50:
            color = 'cyan'
        else:
            color = 'yellow'

        return self.color(f"[{bar}]", color) + f" {percent:.1f}%"

    def fetch_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Fetch job status from API"""
        try:
            url = f"{self.api_url}/api/cloudpath-dpsk/jobs/{job_id}/status"
            response = requests.get(url, timeout=5)

            if response.status_code == 404:
                print(self.color(f"Job {job_id} not found", 'red'))
                return None

            response.raise_for_status()
            return response.json()

        except requests.RequestException as e:
            print(self.color(f"Error fetching status: {str(e)}", 'red'))
            return None

    def render_header(self, job_data: Dict[str, Any]):
        """Render header section"""
        job_id = job_data['job_id']
        status = job_data['status']

        print("╔" + "═" * 78 + "╗")
        print("║" + self.color(" Cloudpath DPSK Migration Workflow", 'bold').center(88) + "║")
        print("╠" + "═" * 78 + "╣")
        print(f"║  Job ID: {self.color(job_id, 'cyan'):<70}║")

        status_color = {
            'RUNNING': 'cyan',
            'COMPLETED': 'green',
            'FAILED': 'red',
            'PARTIAL': 'yellow'
        }.get(status, 'gray')

        icon = self.ICONS.get(status, '⚪')
        status_text = f"{icon} {status}"
        print(f"║  Status: {self.color(status_text, status_color):<70}║")
        print("╠" + "═" * 78 + "╣")

    def render_progress(self, progress: Dict[str, Any]):
        """Render overall progress"""
        total = progress.get('total_tasks', 0)
        completed = progress.get('completed', 0)
        failed = progress.get('failed', 0)
        percent = progress.get('percent', 0)

        print(f"║  Overall Progress: {completed + failed}/{total} tasks" + " " * 42 + "║")
        print("║  " + self.draw_progress_bar(percent, 70) + " ║")

        if failed > 0:
            print(f"║  {self.color('⚠️  ' + str(failed) + ' tasks failed', 'yellow'):<70}║")

        print("╠" + "═" * 78 + "╣")

    def render_current_phase(self, current_phase: Optional[Dict[str, Any]]):
        """Render current phase info"""
        if not current_phase:
            return

        name = current_phase['name']
        completed = current_phase.get('tasks_completed', 0)
        total = current_phase.get('tasks_total', 0)
        percent = (completed / total * 100) if total > 0 else 0

        print(f"║  Current Phase: {self.color(name, 'cyan'):<62}║")
        print(f"║  Tasks: {completed}/{total}" + " " * 65 + "║")
        print("║  " + self.draw_progress_bar(percent, 70) + " ║")
        print("╠" + "═" * 78 + "╣")

    def render_phases(self, phases: list):
        """Render phase summary"""
        print("║  " + self.color("Phase Summary:", 'bold') + " " * 64 + "║")
        print("║" + " " * 78 + "║")

        for phase in phases:
            name = phase['name']
            status = phase['status']
            duration = self.format_duration(phase.get('duration_seconds'))

            icon = self.ICONS.get(status, '⚪')
            status_color = {
                'COMPLETED': 'green',
                'RUNNING': 'cyan',
                'PENDING': 'gray',
                'FAILED': 'red',
                'SKIPPED': 'yellow'
            }.get(status, 'gray')

            # Truncate name if too long
            display_name = name[:45] + '...' if len(name) > 45 else name

            status_text = self.color(f"{icon} {status:<10}", status_color)
            duration_text = self.color(f"({duration})", 'gray')

            print(f"║  {status_text}  {display_name:<45}  {duration_text:<15}║")

        print("╠" + "═" * 78 + "╣")

    def render_resources(self, resources: Dict[str, list]):
        """Render created resources summary"""
        if not resources:
            return

        print("║  " + self.color("Created Resources:", 'bold') + " " * 59 + "║")
        print("║" + " " * 78 + "║")

        resource_names = {
            'identity_groups': 'Identity Groups',
            'dpsk_pools': 'DPSK Pools',
            'passphrases': 'Passphrases',
            'policy_sets': 'Policy Sets'
        }

        for key, name in resource_names.items():
            count = len(resources.get(key, []))
            if count > 0:
                count_text = self.color(str(count), 'cyan')
                print(f"║  • {name}: {count_text:<64}║")

        print("╠" + "═" * 78 + "╣")

    def render_errors(self, errors: list):
        """Render errors if any"""
        if not errors:
            return

        print("║  " + self.color("Errors:", 'red') + " " * 70 + "║")
        print("║" + " " * 78 + "║")

        for error in errors[:5]:  # Show max 5 errors
            # Truncate long errors
            display_error = error[:72] + '...' if len(error) > 72 else error
            print(f"║  ❌ {display_error:<73}║")

        if len(errors) > 5:
            remaining = len(errors) - 5
            print(f"║  {self.color(f'...and {remaining} more errors', 'gray'):<70}║")

        print("╠" + "═" * 78 + "╣")

    def render_footer(self):
        """Render footer"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"║  Last updated: {self.color(timestamp, 'gray'):<62}║")
        print("║  " + self.color("Press Ctrl+C to exit", 'gray') + " " * 55 + "║")
        print("╚" + "═" * 78 + "╝")

    def render(self, job_data: Dict[str, Any]):
        """Render complete dashboard"""
        self.clear_screen()

        self.render_header(job_data)
        self.render_progress(job_data['progress'])

        if job_data.get('current_phase'):
            self.render_current_phase(job_data['current_phase'])

        self.render_phases(job_data['phases'])
        self.render_resources(job_data.get('created_resources', {}))

        if job_data.get('errors'):
            self.render_errors(job_data['errors'])

        self.render_footer()

    def watch(self, job_id: str, refresh_interval: int = 2):
        """Watch job progress in real-time"""
        print(self.color(f"\n🔍 Watching workflow job: {job_id}\n", 'bold'))

        try:
            while True:
                job_data = self.fetch_status(job_id)

                if not job_data:
                    time.sleep(refresh_interval)
                    continue

                self.render(job_data)
                self.last_status = job_data['status']

                # Exit if job completed or failed
                if job_data['status'] in ['COMPLETED', 'FAILED', 'PARTIAL']:
                    print(f"\n{self.color('Job finished!', 'bold')} Final status: {self.color(job_data['status'], 'cyan')}\n")
                    break

                time.sleep(refresh_interval)

        except KeyboardInterrupt:
            print(f"\n\n{self.color('Monitoring stopped.', 'yellow')}\n")
            sys.exit(0)


def main():
    parser = argparse.ArgumentParser(
        description='Watch workflow job progress in real-time',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python watch_workflow.py abc-123-def-456
  python watch_workflow.py abc-123 --api-url http://api.example.com
  python watch_workflow.py abc-123 --refresh 1
        """
    )

    parser.add_argument(
        'job_id',
        help='Workflow job ID to monitor'
    )

    parser.add_argument(
        '--api-url',
        default='http://localhost:8000',
        help='API base URL (default: http://localhost:8000)'
    )

    parser.add_argument(
        '--refresh',
        type=int,
        default=2,
        help='Refresh interval in seconds (default: 2)'
    )

    args = parser.parse_args()

    viewer = ProgressViewer(args.api_url)
    viewer.watch(args.job_id, args.refresh)


if __name__ == '__main__':
    main()
