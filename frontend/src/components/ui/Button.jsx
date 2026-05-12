import React from 'react';

const Button = ({ children, variant = 'primary', className = '', style = {}, ...props }) => {
  const base = {
    padding: '12px 22px',
    borderRadius: '999px',
    fontSize: '15px',
    fontWeight: 700,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'all 0.18s',
    cursor: 'pointer',
    fontFamily: 'inherit',
  };

  const variants = {
    primary: {
      backgroundColor: 'var(--primary)',
      color: 'white',
      border: 'none',
      boxShadow: '0 4px 14px rgba(255,126,103,0.30)',
    },
    dark: {
      backgroundColor: 'var(--btn-dark)',
      color: 'white',
      border: 'none',
      boxShadow: '0 6px 24px rgba(0,0,0,0.22)',
    },
    secondary: {
      backgroundColor: 'var(--primary-light)',
      color: 'var(--primary)',
      border: 'none',
      boxShadow: 'none',
    },
    outline: {
      backgroundColor: 'var(--white)',
      color: 'var(--text-dark)',
      border: '1.5px solid var(--border-color)',
      boxShadow: 'none',
    },
    ghost: {
      backgroundColor: 'transparent',
      color: 'var(--text-gray)',
      border: 'none',
    },
  };

  const vStyle = variants[variant] || variants.primary;

  return (
    <button
      style={{ ...base, ...vStyle, ...style }}
      className={className}
      onMouseOver={e => {
        e.currentTarget.style.transform = 'translateY(-2px)';
        e.currentTarget.style.boxShadow = 'var(--shadow-md)';
      }}
      onMouseOut={e => {
        e.currentTarget.style.transform = 'none';
        e.currentTarget.style.boxShadow = vStyle.boxShadow || 'none';
      }}
      onPointerDown={e => { e.currentTarget.style.transform = 'scale(0.96)'; }}
      onPointerUp={e => { e.currentTarget.style.transform = 'translateY(-2px)'; }}
      {...props}
    >
      {children}
    </button>
  );
};

export default Button;
