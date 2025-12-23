import JobMonitorView from './JobMonitorView';
import type { JobResult } from './JobMonitorView';

export type { JobResult };

interface JobMonitorModalProps {
  jobId: string;
  onClose: () => void;
  isOpen: boolean;
  onCleanup?: (jobId: string) => void;
  onJobComplete?: (result: JobResult) => void;
}

const JobMonitorModal = ({ jobId, onClose, isOpen, onCleanup, onJobComplete }: JobMonitorModalProps) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-7xl max-h-[95vh] overflow-hidden flex flex-col">
        {/* Modal Header */}
        <div className="bg-gradient-to-r from-blue-600 to-cyan-600 text-white px-6 py-4 flex justify-between items-center">
          <div>
            <h3 className="text-2xl font-bold">Job Monitor</h3>
            <p className="text-blue-100 text-sm font-mono">{jobId}</p>
          </div>
          <button
            onClick={onClose}
            className="text-white hover:text-gray-200 text-3xl font-bold w-10 h-10 flex items-center justify-center rounded-full hover:bg-white hover:bg-opacity-20 transition-colors"
            aria-label="Close modal"
          >
            ×
          </button>
        </div>

        {/* Modal Body - Scrollable */}
        <div className="overflow-y-auto flex-1">
          <JobMonitorView
            jobId={jobId}
            onClose={onClose}
            showFullPageLink={true}
            onCleanup={onCleanup}
            onJobComplete={onJobComplete}
          />
        </div>

        {/* Modal Footer */}
        <div className="bg-gray-50 px-6 py-4 flex justify-end border-t">
          <button
            onClick={onClose}
            className="px-6 py-2 bg-gray-600 text-white rounded hover:bg-gray-700 font-semibold transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};

export default JobMonitorModal;
