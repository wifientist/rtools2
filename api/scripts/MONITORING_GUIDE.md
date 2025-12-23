# Workflow Monitoring Guide

Two ways to monitor workflow progress in real-time!

---

## Option 1: Terminal Progress Viewer (For Development)

### Usage:
```bash
cd /home/omni/code/rtools2/api
python scripts/watch_workflow.py <job_id>
```

### Example:
```bash
# After starting a migration, you'll get a job_id back
# Use it to watch progress:
python scripts/watch_workflow.py abc-123-def-456-ghi

# Custom API URL:
python scripts/watch_workflow.py abc-123 --api-url http://api.example.com

# Faster refresh (every 1 second):
python scripts/watch_workflow.py abc-123 --refresh 1
```

### What You'll See:
```
╔══════════════════════════════════════════════════════════════╗
║        Cloudpath DPSK Migration Workflow                     ║
╠══════════════════════════════════════════════════════════════╣
║  Job ID: abc-123-def-456                                     ║
║  Status: 🔄 RUNNING                                          ║
╠══════════════════════════════════════════════════════════════╣
║  Overall Progress: 905/2000 tasks                            ║
║  [████████████░░░░░░░░░░] 45.2%                              ║
╠══════════════════════════════════════════════════════════════╣
║  Current Phase: Create DPSK Passphrases                      ║
║  Tasks: 900/2000                                             ║
║  [████████░░░░░░░░░░░░░] 45.0%                               ║
╠══════════════════════════════════════════════════════════════╣
║  Phase Summary:                                              ║
║                                                              ║
║  ✅ COMPLETED   Parse and Validate              (2s)        ║
║  ✅ COMPLETED   Create Identity Groups          (15s)       ║
║  ✅ COMPLETED   Create DPSK Pools               (23s)       ║
║  ⏭️  SKIPPED     Create Policy Sets              (N/A)       ║
║  ⏭️  SKIPPED     Attach Policies                 (N/A)       ║
║  🔄 RUNNING     Create Passphrases              (N/A)       ║
║  ⏸️  PENDING     Activate on Networks            (N/A)       ║
║  ⏸️  PENDING     Audit Results                   (N/A)       ║
╠══════════════════════════════════════════════════════════════╣
║  Created Resources:                                          ║
║                                                              ║
║  • Identity Groups: 5                                        ║
║  • DPSK Pools: 5                                             ║
║  • Passphrases: 900                                          ║
╠══════════════════════════════════════════════════════════════╣
║  Last updated: 2025-12-19 15:32:45                           ║
║  Press Ctrl+C to exit                                        ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Option 2: Server-Sent Events (SSE) - For Frontend Dashboard

### Endpoint:
```
GET /api/cloudpath-dpsk/jobs/{job_id}/stream
```

### Frontend JavaScript Example:
```javascript
// Connect to SSE stream
const jobId = 'abc-123-def-456';
const eventSource = new EventSource(
  `/api/cloudpath-dpsk/jobs/${jobId}/stream`
);

// Listen for connection
eventSource.addEventListener('connected', (e) => {
  const data = JSON.parse(e.data);
  console.log('Connected to job:', data.job_id);
});

// Listen for progress updates
eventSource.addEventListener('progress', (e) => {
  const data = JSON.parse(e.data);
  console.log(`Progress: ${data.percent}%`);

  // Update progress bar
  document.getElementById('progress-bar').style.width = `${data.percent}%`;
  document.getElementById('progress-text').textContent =
    `${data.completed}/${data.total_tasks} tasks`;
});

// Listen for phase events
eventSource.addEventListener('phase_started', (e) => {
  const data = JSON.parse(e.data);
  console.log(`Phase started: ${data.phase_name}`);
  showNotification(`Starting ${data.phase_name}...`);
});

eventSource.addEventListener('phase_completed', (e) => {
  const data = JSON.parse(e.data);
  console.log(`Phase completed: ${data.phase_name} (${data.status})`);
  console.log(`  Completed: ${data.completed_tasks}, Failed: ${data.failed_tasks}`);
});

// Listen for task events
eventSource.addEventListener('task_completed', (e) => {
  const data = JSON.parse(e.data);
  console.log(`Task completed: ${data.task_name} (${data.status})`);
});

// Listen for job completion
eventSource.addEventListener('job_completed', (e) => {
  const data = JSON.parse(e.data);
  console.log('Job completed!', data);
  eventSource.close();
  showSuccessModal(data);
});

eventSource.addEventListener('job_failed', (e) => {
  const data = JSON.parse(e.data);
  console.error('Job failed!', data);
  eventSource.close();
  showErrorModal(data);
});

// Handle errors
eventSource.onerror = (e) => {
  console.error('SSE connection error:', e);
  eventSource.close();
};
```

### React Example:
```typescript
import { useEffect, useState } from 'react';

function WorkflowMonitor({ jobId }) {
  const [progress, setProgress] = useState({ percent: 0, completed: 0, total: 0 });
  const [currentPhase, setCurrentPhase] = useState(null);
  const [status, setStatus] = useState('RUNNING');

  useEffect(() => {
    const eventSource = new EventSource(
      `/api/cloudpath-dpsk/jobs/${jobId}/stream`
    );

    eventSource.addEventListener('progress', (e) => {
      const data = JSON.parse(e.data);
      setProgress(data);
    });

    eventSource.addEventListener('phase_started', (e) => {
      const data = JSON.parse(e.data);
      setCurrentPhase(data.phase_name);
    });

    eventSource.addEventListener('job_completed', (e) => {
      const data = JSON.parse(e.data);
      setStatus(data.status);
      eventSource.close();
    });

    eventSource.addEventListener('job_failed', (e) => {
      setStatus('FAILED');
      eventSource.close();
    });

    return () => eventSource.close();
  }, [jobId]);

  return (
    <div className="workflow-monitor">
      <h2>Workflow Status: {status}</h2>
      {currentPhase && <p>Current Phase: {currentPhase}</p>}
      <div className="progress-bar">
        <div
          className="progress-fill"
          style={{ width: `${progress.percent}%` }}
        />
      </div>
      <p>{progress.completed} / {progress.total} tasks ({progress.percent}%)</p>
    </div>
  );
}
```

---

## Event Types

### Available SSE Events:

| Event Type | Description | Data Fields |
|------------|-------------|-------------|
| `connected` | Initial connection | `job_id` |
| `status` | Current job status | `status`, `progress` |
| `job_started` | Workflow started | `job_id`, `workflow_name`, `total_phases` |
| `phase_started` | Phase started | `phase_id`, `phase_name`, `total_tasks` |
| `phase_completed` | Phase completed | `phase_id`, `phase_name`, `status`, `completed_tasks`, `failed_tasks`, `duration_seconds` |
| `task_started` | Task started | `phase_id`, `task_id`, `task_name` |
| `task_completed` | Task completed | `phase_id`, `task_id`, `task_name`, `status`, `duration_seconds` |
| `progress` | Progress update | `total_tasks`, `completed`, `failed`, `pending`, `percent` |
| `job_completed` | Job finished successfully | `status`, `summary`, `duration_seconds` |
| `job_failed` | Job failed | `errors`, `summary` |

---

## Testing Both Methods

### 1. Start a migration:
```bash
curl -X POST http://localhost:8000/api/cloudpath-dpsk/import \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "controller_id": 1,
    "venue_id": "venue-123",
    "dpsk_data": {...},
    "options": {"just_copy_dpsks": true}
  }'
```

### 2. Get the job_id from response:
```json
{
  "job_id": "abc-123-def-456",
  "status": "RUNNING"
}
```

### 3. Watch in terminal:
```bash
python scripts/watch_workflow.py abc-123-def-456
```

### 4. Or test SSE with curl:
```bash
curl -N http://localhost:8000/api/cloudpath-dpsk/jobs/abc-123-def-456/stream
```

---

## Tips

1. **Terminal Viewer**: Best for quick dev testing, doesn't require browser
2. **SSE Stream**: Best for production frontend dashboards with live updates
3. **Polling** (`/status` endpoint): Fallback if SSE not supported
4. **Event Frequency**: Progress events published after each phase completes
5. **Auto-close**: SSE stream closes automatically when job completes/fails
6. **Reconnection**: Frontend should handle reconnection on network issues

---

## Troubleshooting

### Terminal viewer not working?
```bash
# Check if Redis is running
docker ps | grep redis

# Check if API is running
curl http://localhost:8000/api/status
```

### SSE not receiving events?
- Check browser console for connection errors
- Verify job_id exists: `GET /api/cloudpath-dpsk/jobs/{job_id}/status`
- Check Redis pub/sub is working: `redis-cli PUBSUB CHANNELS`

### Events not being published?
- Check API logs for event publisher errors
- Verify Redis connection is active
- Ensure workflow engine has `event_publisher` configured
