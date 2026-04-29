import React from 'react';

const Button = ({ children, variant = 'primary', className = '', style = {}, ...props }) => {
  const baseStyles = {
    padding: '14px 24px',
    borderRadius: '12px',
    fontSize: '16px',
    fontWeight: '700',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'all 0.2s',
    cursor: 'pointer',
  };

  const variants = {
    primary: {
      backgroundColor: 'var(--primary)',
      color: 'white',
      border: 'none',
      boxShadow: 'var(--shadow-sm)',
    },
    secondary: {
      backgroundColor: 'var(--primary-light)',
      color: 'var(--primary)',
      border: 'none',
      boxShadow: 'var(--shadow-sm)',
    },
    outline: {
      backgroundColor: 'var(--white)',
      color: 'var(--text-dark)',
      border: '1px solid var(--border-color)',
      boxShadow: 'var(--shadow-sm)',
    },
    ghost: {
      backgroundColor: 'transparent',
      color: 'var(--text-gray)',
      border: 'none',
    }
  };

  return (
    <button 
      style={{ ...baseStyles, ...variants[variant], ...style }}
      className={className}
      onMouseOver={(e) => {
        if(variant === 'primary') e.currentTarget.style.transform = 'translateY(-2px)';
        e.currentTarget.style.boxShadow = 'var(--shadow-md)';
      }}
      onMouseOut={(e) => {
        if(variant === 'primary') e.currentTarget.style.transform = 'none';
        e.currentTarget.style.boxShadow = variants[variant].boxShadow || 'none';
      }}
      onPointerDown={(e) => e.currentTarget.style.transform = 'scale(0.96)'}
      onPointerUp={(e) => {
        if(variant === 'primary') e.currentTarget.style.transform = 'translateY(-2px)';
        else e.currentTarget.style.transform = 'none';
      }}
      {...props}
    >
      {children}
    </button>
  );
};

export default Button;
