import { Cable, Keyboard, Monitor, Radio, Timer } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ProofModuleModel } from "../state/proof";

interface ProofRailProps {
  modules: ProofModuleModel[];
}

const icons: Record<ProofModuleModel["id"], LucideIcon> = {
  session: Timer,
  video: Monitor,
  uart: Cable,
  hid: Keyboard,
  target: Radio,
};

export function ProofRail({ modules }: ProofRailProps) {
  return (
    <section className="proof-rail" aria-label="End-to-end proof rail">
      <div className="proof-line" aria-hidden="true" />
      <div className="proof-modules">
        {modules.map((module) => {
          const Icon = icons[module.id];
          return (
            <article className="proof-module" key={module.id}>
              <div className="proof-module__heading">
                <h2>{module.title}</h2>
                <span className={`proof-state proof-state--${module.tone}`}>
                  <span className="status-dot" aria-hidden="true" />
                  {module.state}
                </span>
              </div>
              <div className="proof-module__body">
                <dl>
                  {module.fields.map((field) => (
                    <div key={field.label}>
                      <dt>{field.label}</dt>
                      <dd>{field.value}</dd>
                    </div>
                  ))}
                </dl>
                <Icon size={30} strokeWidth={1.5} aria-hidden="true" />
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
