package com.example;

public interface UserRepository {
    // insert into users (name) values (?)
    User save(User user);
}
