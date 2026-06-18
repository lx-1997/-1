import React, { createContext, useCallback, useContext, useMemo, useRef } from 'react';

export interface ModuleContextData {
  module: string;
  title: string;
  summary: string;
  data?: Record<string, unknown>;
  lastUpdated: number;
}

interface ModuleContextValue {
  currentContext: ModuleContextData | null;
  registerModule: (ctx: ModuleContextData) => void;
  unregisterModule: (module: string) => void;
}

const ModuleContext = createContext<ModuleContextValue>({
  currentContext: null,
  registerModule: () => {},
  unregisterModule: () => {},
});

export function ModuleContextProvider({ children }: { children: React.ReactNode }) {
  const ref = useRef<ModuleContextData | null>(null);

  const registerModule = useCallback((ctx: ModuleContextData) => {
    ref.current = ctx;
  }, []);

  const unregisterModule = useCallback((module: string) => {
    if (ref.current?.module === module) {
      ref.current = null;
    }
  }, []);

  const value = useMemo(() => ({
    get currentContext() { return ref.current; },
    registerModule,
    unregisterModule,
  }), [registerModule, unregisterModule]);

  return (
    <ModuleContext.Provider value={value}>
      {children}
    </ModuleContext.Provider>
  );
}

export function useModuleContext(): ModuleContextValue {
  return useContext(ModuleContext);
}

export function useRegisterModuleContext(ctx: ModuleContextData | null) {
  const { registerModule, unregisterModule } = useModuleContext();
  const prevRef = useRef<string | null>(null);

  React.useEffect(() => {
    if (ctx) {
      registerModule(ctx);
      prevRef.current = ctx.module;
      return () => {
        if (prevRef.current) {
          unregisterModule(prevRef.current);
        }
      };
    }
    return undefined;
  }, [ctx, registerModule, unregisterModule]);
}