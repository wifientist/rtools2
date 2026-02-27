import { Check } from 'lucide-react';

interface Step {
  number: number;
  label: string;
  color: string;
}

const STEPS: Step[] = [
  { number: 1, label: 'Source', color: 'blue' },
  { number: 2, label: 'Extract', color: 'blue' },
  { number: 3, label: 'Destination', color: 'indigo' },
  { number: 4, label: 'Review', color: 'purple' },
  { number: 5, label: 'Execute', color: 'orange' },
  { number: 6, label: 'Results', color: 'green' },
];

interface WizardStepperProps {
  currentStep: number;
  highestStepReached?: number;
  onStepClick?: (step: number) => void;
}

export default function WizardStepper({ currentStep, highestStepReached, onStepClick }: WizardStepperProps) {
  const maxStep = highestStepReached ?? currentStep;

  return (
    <div className="flex items-center justify-between mb-6">
      {STEPS.map((step, idx) => {
        const isCompleted = currentStep > step.number;
        const isCurrent = currentStep === step.number;
        const isReachable = step.number <= maxStep && step.number !== currentStep;
        const isClickable = onStepClick && isReachable;

        return (
          <div key={step.number} className="flex items-center flex-1">
            {/* Step circle + label */}
            <button
              onClick={() => isClickable && onStepClick(step.number)}
              disabled={!isClickable}
              className={`flex flex-col items-center ${isClickable ? 'cursor-pointer' : 'cursor-default'}`}
            >
              <div
                className={`w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold transition-colors ${
                  isCompleted
                    ? 'bg-green-600 text-white'
                    : isCurrent
                    ? `bg-${step.color}-600 text-white ring-2 ring-${step.color}-300 ring-offset-1`
                    : isReachable
                    ? 'bg-green-100 text-green-700 border-2 border-green-400'
                    : 'bg-gray-200 text-gray-500'
                }`}
                style={
                  isCurrent
                    ? { backgroundColor: getColor(step.color), color: 'white', boxShadow: `0 0 0 3px ${getColor(step.color, 0.3)}` }
                    : isCompleted
                    ? { backgroundColor: '#16a34a', color: 'white' }
                    : undefined
                }
              >
                {isCompleted ? <Check size={16} /> : step.number}
              </div>
              <span
                className={`text-xs mt-1 ${
                  isCurrent ? 'font-semibold text-gray-800' : 'text-gray-500'
                }`}
              >
                {step.label}
              </span>
            </button>

            {/* Connector line */}
            {idx < STEPS.length - 1 && (
              <div
                className={`flex-1 h-0.5 mx-2 ${
                  maxStep > step.number ? 'bg-green-400' : 'bg-gray-200'
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

function getColor(name: string, alpha = 1): string {
  const colors: Record<string, string> = {
    blue: `rgba(37, 99, 235, ${alpha})`,
    indigo: `rgba(79, 70, 229, ${alpha})`,
    purple: `rgba(147, 51, 234, ${alpha})`,
    orange: `rgba(234, 88, 12, ${alpha})`,
    green: `rgba(22, 163, 74, ${alpha})`,
  };
  return colors[name] || colors.blue;
}
