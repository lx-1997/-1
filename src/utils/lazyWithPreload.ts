import React, { lazy } from 'react';

type ComponentModule<T extends React.ComponentType<any>> = {
  default: T;
};

export type PreloadableComponent<T extends React.ComponentType<any>> = React.LazyExoticComponent<T> & {
  preload: () => Promise<ComponentModule<T>>;
};

export function lazyWithPreload<T extends React.ComponentType<any>>(
  factory: () => Promise<ComponentModule<T>>
): PreloadableComponent<T> {
  let promise: Promise<ComponentModule<T>> | undefined;

  const load = () => {
    if (!promise) {
      promise = factory().catch(error => {
        promise = undefined;
        throw error;
      });
    }
    return promise;
  };

  const Component = lazy(load) as PreloadableComponent<T>;
  Component.preload = load;

  return Component;
}
