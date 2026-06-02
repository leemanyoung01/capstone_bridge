import React from 'react';

const VARIANT_CLASS = {
  primary:   'btn btn-primary',
  dark:      'btn btn-primary',
  secondary: 'btn btn-ghost',
  outline:   'btn btn-ghost',
  ghost:     'btn btn-ghost',
};

const Button = ({ children, variant = 'primary', className = '', style = {}, ...props }) => {
  const cls = `${VARIANT_CLASS[variant] || 'btn btn-primary'} ${className}`.trim();
  return (
    <button className={cls} style={style} {...props}>
      {children}
    </button>
  );
};

export default Button;
